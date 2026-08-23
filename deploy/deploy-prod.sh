#!/usr/bin/env bash
# Deploy production — the real thing: compose.yaml (own Caddy, real TLS), real user data.
#
#   deploy/deploy-prod.sh                 # deploy main
#   deploy/deploy-prod.sh --push          # push main first, then deploy
#   SKIP_BACKUP=1 deploy/deploy-prod.sh   # skip the pre-migration backup (not recommended)
#
# Same shape as deploy/deploy-dev.sh — see its own comment for the general design (fetch the
# pushed branch, pull the image .github/workflows/build-and-push.yml already built, migrate
# explicitly, restart, wait for healthy) — with what production specifically needs on top:
#
#   - compose.yaml, not compose.behind-proxy.yaml: production runs its own Caddy/TLS.
#   - Refuses anything but `main` by default (ALLOW_NON_MAIN=1 to override) — a deploy target
#     this permanent shouldn't ship a feature branch by accident.
#   - Backs the database up (deploy/backup.sh) before migrating — a migration is the one step
#     here that isn't just "restart with new code," so it gets a fresh safety net first.
#   - Restarts worker and beat alongside web. deploy-dev.sh only restarts web (that gap is
#     noted there); in production, a worker or beat left running stale task code — a signature
#     changed, a schedule changed — is a real bug, not just a dev inconvenience.
#   - Verifies over the public URL (curl https://$BASE_DOMAIN/healthz through Caddy), since
#     web has no host-published port here to hit directly the way the dev instance does.
#
# No default SSH_HOST/REMOTE_DIR, unlike deploy-dev.sh — guessing wrong here is a much worse
# mistake than for a throwaway test instance, so this refuses to run until you say exactly
# where "production" is.
set -Eeuo pipefail

SSH_HOST="${SSH_HOST:?set SSH_HOST — the production server, e.g. SSH_HOST=1.2.3.4}"
SSH_USER="${SSH_USER:?set SSH_USER}"
REMOTE_DIR="${REMOTE_DIR:?set REMOTE_DIR, e.g. /home/bernard/RosterChief}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
BASE_DOMAIN="${BASE_DOMAIN:-rosterchief.app}"
SKIP_BACKUP="${SKIP_BACKUP:-}"

SSH_TARGET="${SSH_USER}@${SSH_HOST}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$BRANCH" != "main" ] && [ -z "${ALLOW_NON_MAIN:-}" ]; then
    die "Refusing to deploy '${BRANCH}' to production — only main ships here. Set ALLOW_NON_MAIN=1 to override."
fi

# --- preflight, locally -----------------------------------------------------
# Same reasoning as deploy-dev.sh: the server deploys what's on the git remote, so unpushed
# commits would silently ship stale code.
git rev-parse --verify --quiet "origin/${BRANCH}" >/dev/null \
    || die "origin/${BRANCH} does not exist. Push the branch first, or pass --push."

if [ "${1:-}" = "--push" ]; then
    say "Pushing ${BRANCH} to origin"
    git push origin "${BRANCH}"
elif [ -n "$(git rev-list "origin/${BRANCH}..HEAD" 2>/dev/null)" ]; then
    die "Local ${BRANCH} is ahead of origin — the server would deploy stale code. Push first, or run with --push."
fi

say "Deploying ${BRANCH} to PRODUCTION (${SSH_TARGET}:${REMOTE_DIR})"

# --- the work, on the server ------------------------------------------------
ssh -o ConnectTimeout=10 "${SSH_TARGET}" bash -s -- "${REMOTE_DIR}" "${BRANCH}" "${COMPOSE_FILE}" "${SKIP_BACKUP}" <<'REMOTE'
set -Eeuo pipefail
REMOTE_DIR="$1"; BRANCH="$2"; COMPOSE_FILE="$3"; SKIP_BACKUP="$4"

step() { printf '\033[1;34m  ->\033[0m %s\n' "$*"; }

cd "$REMOTE_DIR" 2>/dev/null || { echo "ERROR: $REMOTE_DIR not found. Clone the repo there first."; exit 1; }
[ -d .git ] || { echo "ERROR: $REMOTE_DIR is not a git checkout."; exit 1; }

[ -f .env.production ] || { echo "ERROR: .env.production missing (Django config). Copy from .env.production.example."; exit 1; }
[ -f .env ]            || { echo "ERROR: .env missing (compose vars: POSTGRES_PASSWORD, ...). Copy from .env.compose.example."; exit 1; }

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# reset --hard, not pull: a deploy target only receives deploys — see deploy-dev.sh.
step "Fetching ${BRANCH}"
git fetch --quiet origin
git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/${BRANCH}"
echo "     at $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

# db/redis are restart:unless-stopped and should already be up from steady state — this is
# just belt-and-suspenders (e.g. right after a host reboot) before backup.sh execs into them.
step "Starting db + redis"
dc up -d db redis

if [ -z "$SKIP_BACKUP" ]; then
    step "Backing up the database before migrating"
    COMPOSE="docker compose -f $COMPOSE_FILE" ./deploy/backup.sh /var/backups/rosterchief
else
    step "Skipping backup (SKIP_BACKUP set) -- not recommended before a migration"
fi

step "Pulling image for ${BRANCH}"
IMAGE_TAG="$BRANCH" dc pull web worker beat

step "Running migrations"
# -T and </dev/null: this whole script IS ssh's stdin (a heredoc) -- see deploy-dev.sh's own
# comment on why `compose run` needs both or web never restarts.
dc run --rm -T web python manage.py migrate --noinput </dev/null

step "Restarting web, worker, beat"
IMAGE_TAG="$BRANCH" dc up -d --no-deps web worker beat
REMOTE

say "Waiting for https://${BASE_DOMAIN}/healthz"
for attempt in $(seq 1 20); do
    if curl -fsS "https://${BASE_DOMAIN}/healthz" >/dev/null 2>&1; then
        say "Healthy after ${attempt} check(s)."
        exit 0
    fi
    sleep 3
done

echo "ERROR: health check never passed against https://${BASE_DOMAIN}/healthz" >&2
echo "Recent web logs:" >&2
ssh -o ConnectTimeout=10 "${SSH_TARGET}" "cd ${REMOTE_DIR} && docker compose -f ${COMPOSE_FILE} logs --tail 40 web" >&2
exit 1
