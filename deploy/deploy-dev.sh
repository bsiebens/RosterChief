#!/usr/bin/env bash
# Deploy the test instance to the dev server, behind its existing Caddy.
#
#   deploy/deploy-dev.sh            # deploy the current branch
#   BRANCH=main deploy/deploy-dev.sh
#   deploy/deploy-dev.sh --push     # push the branch first, then deploy
#
# Runs FROM your machine, works ON the server over one SSH session: it fetches the pushed
# branch, builds the image, runs migrations explicitly (never from the entrypoint — a
# starting gunicorn worker is a bad place to discover a failed migration), restarts web, and
# waits for /healthz. Any step failing aborts the whole thing with a non-zero exit.
set -Eeuo pipefail

# --- config (override via env) ----------------------------------------------
SSH_HOST="${SSH_HOST:-home.siebens.org}"
SSH_USER="${SSH_USER:-bernard}"
REMOTE_DIR="${REMOTE_DIR:-/home/bernard/RosterChief}"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.behind-proxy.yaml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/healthz}"

SSH_TARGET="${SSH_USER}@${SSH_HOST}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight, locally -----------------------------------------------------
# The server deploys what is on the git remote, so unpushed commits would silently ship stale
# code. Catch that here rather than after a confusing "why isn't my change live" round trip.
git rev-parse --verify --quiet "origin/${BRANCH}" >/dev/null \
    || die "origin/${BRANCH} does not exist. Push the branch first, or pass --push."

if [ "${1:-}" = "--push" ]; then
    say "Pushing ${BRANCH} to origin"
    git push origin "${BRANCH}"
elif [ -n "$(git rev-list "origin/${BRANCH}..HEAD" 2>/dev/null)" ]; then
    die "Local ${BRANCH} is ahead of origin — the server would deploy stale code. Push first, or run with --push."
fi

say "Deploying ${BRANCH} to ${SSH_TARGET}:${REMOTE_DIR}"

# --- the work, on the server ------------------------------------------------
# One SSH session runs the whole remote script; args are passed positionally so nothing has to
# be re-quoted inside the heredoc.
ssh -o ConnectTimeout=10 "${SSH_TARGET}" bash -s -- "${REMOTE_DIR}" "${BRANCH}" "${COMPOSE_FILE}" "${HEALTH_URL}" <<'REMOTE'
set -Eeuo pipefail
REMOTE_DIR="$1"; BRANCH="$2"; COMPOSE_FILE="$3"; HEALTH_URL="$4"

step() { printf '\033[1;34m  ->\033[0m %s\n' "$*"; }

cd "$REMOTE_DIR" 2>/dev/null || { echo "ERROR: $REMOTE_DIR not found. Clone the repo there first."; exit 1; }
[ -d .git ] || { echo "ERROR: $REMOTE_DIR is not a git checkout."; exit 1; }

# The env files carry secrets and are never committed, so they must already be on the server.
# Fail loudly rather than boot a half-configured stack.
[ -f .env.production ] || { echo "ERROR: .env.production missing (Django config). Copy from .env.production.example."; exit 1; }
[ -f .env ]            || { echo "ERROR: .env missing (compose vars: POSTGRES_PASSWORD, ...). Copy from .env.compose.example."; exit 1; }

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# reset --hard, not pull: a deploy target only receives deploys, so make it exactly match the
# remote branch rather than risk a merge conflict from drift no one meant to leave there.
step "Fetching ${BRANCH}"
git fetch --quiet origin
git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/${BRANCH}"
echo "     at $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

step "Building image"
dc build

# Bring the data services up first and wait for Postgres, so the migration below has something
# to connect to on a cold start.
step "Starting db + redis"
dc up -d db redis

step "Running migrations"
dc run --rm web python manage.py migrate --noinput

# Recreate only web, with the freshly built image. db and redis keep running untouched.
step "Restarting web"
dc up -d --no-deps web

step "Waiting for /healthz"
for attempt in $(seq 1 20); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        echo "     healthy after ${attempt} check(s)"
        exit 0
    fi
    sleep 3
done

echo "ERROR: health check never passed. Recent web logs:"
dc logs --tail 40 web
exit 1
REMOTE

say "Done. https://<your-test-domain>/healthz should return ok."
