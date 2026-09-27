#!/usr/bin/env bash
# Deploy production from your own machine -- no need to log into the server.
#
#   deploy/deploy-prod.sh                        # deploy the latest main (image :main)
#   deploy/deploy-prod.sh --version v0.7.8       # deploy (or roll back to) an exact release
#   deploy/deploy-prod.sh --push                 # push local main first, then deploy it
#   SKIP_BACKUP=1 deploy/deploy-prod.sh          # skip the pre-migration backup (not recommended)
#
# What it does is exactly the manual routine, run for you over one SSH session:
#
#   1. locally: refuse to deploy code that isn't on GitHub yet (the server pulls from there);
#   2. on the server: `git fetch`, then check out origin/main (or the release tag) with
#      `reset --hard` -- a deploy target only receives deploys, it never has work of its own.
#      Untracked files (.env, .env.production, backups) are never touched by this;
#   3. on the server: check the env files carry every key the new code requires, *before*
#      anything is stopped, backed up or migrated;
#   4. on the server: run deploy/deploy-local.sh from that fresh checkout -- the same script you
#      would run by hand after logging in (db/redis/caddy up -> backup -> pull image -> migrate
#      -> restart web -> wait for https://$BASE_DOMAIN/healthz). Running the *checked-out*
#      copy means the deploy steps always match the code being deployed.
#
# The image is the one .github/workflows/build-and-push.yml built: `:main` for the latest main,
# `:vX.Y.Z` for a release (only main's CI run creates those tags).
#
# Where production is comes from SSH_HOST / SSH_USER / REMOTE_DIR (and optionally
# SSH_KEY_FILE). There are deliberately no defaults -- keep them in a gitignored file:
#
#   cp deploy/.env.deploy.example deploy/.env.prod.local
#   source deploy/.env.prod.local && deploy/deploy-prod.sh
set -Eeuo pipefail

SSH_HOST="${SSH_HOST:?set SSH_HOST -- the production server, e.g. SSH_HOST=1.2.3.4 (see deploy/.env.deploy.example)}"
SSH_USER="${SSH_USER:?set SSH_USER}"
SSH_KEY_FILE="${SSH_KEY_FILE:-}"
REMOTE_DIR="${REMOTE_DIR:?set REMOTE_DIR, e.g. /home/bernard/RosterChief}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
BASE_DOMAIN="${BASE_DOMAIN:-rosterchief.app}"
# "0", never empty: an empty argument can vanish when ssh rebuilds the remote command line.
SKIP_BACKUP="${SKIP_BACKUP:-0}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

VERSION=""
PUSH=0
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:?--version needs a tag, e.g. --version v0.7.8}"; shift 2 ;;
        --push) PUSH=1; shift ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) die "Unknown argument '$1' (see --help)." ;;
    esac
done

SSH_TARGET="${SSH_USER}@${SSH_HOST}"
SSH_OPTS=(-o ConnectTimeout=10)
[ -n "$SSH_KEY_FILE" ] && SSH_OPTS+=(-i "$SSH_KEY_FILE" -o IdentitiesOnly=yes)

# --- preflight, locally -----------------------------------------------------
git fetch --quiet origin --tags
if [ -n "$VERSION" ]; then
    [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--version must look like v1.2.3, got '$VERSION'."
    [ "$PUSH" = "0" ] || die "--push and --version don't mix: a release is already on GitHub."
    git rev-parse --verify --quiet "refs/tags/${VERSION}" >/dev/null \
        || die "Tag ${VERSION} doesn't exist on GitHub. Releases are tagged by CI when a release PR merges into main."
    GIT_REF="$VERSION"
    IMAGE_TAG="$VERSION"
    WHAT="release ${VERSION}"
else
    if [ "$PUSH" = "1" ]; then
        say "Pushing main to origin"
        git push origin main
        git fetch --quiet origin
    elif [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] && [ -n "$(git rev-list origin/main..HEAD 2>/dev/null)" ]; then
        die "Your local main has commits GitHub doesn't -- the server would deploy without them. Push first, or run with --push."
    fi
    GIT_REF="main"
    IMAGE_TAG="main"
    WHAT="main ($(git rev-parse --short origin/main): $(git log -1 --pretty=%s origin/main))"
fi

say "Deploying ${WHAT} to PRODUCTION (${SSH_TARGET}:${REMOTE_DIR}), image :${IMAGE_TAG}"

# --- the work, on the server ------------------------------------------------
ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" bash -s -- \
    "${REMOTE_DIR}" "${GIT_REF}" "${IMAGE_TAG}" "${COMPOSE_FILE}" "${BASE_DOMAIN}" "${SKIP_BACKUP}" <<'REMOTE'
set -Eeuo pipefail
REMOTE_DIR="$1"; GIT_REF="$2"; IMAGE_TAG="$3"; COMPOSE_FILE="$4"; BASE_DOMAIN="$5"; SKIP_BACKUP="$6"

step() { printf '\033[1;34m  ->\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

cd "$REMOTE_DIR" 2>/dev/null || fail "$REMOTE_DIR not found on the server. Clone the repo there first (see DEPLOYMENT.md, First deploy)."
[ -d .git ] || fail "$REMOTE_DIR is not a git checkout."

step "Updating the checkout to ${GIT_REF}"
git fetch --quiet --tags origin
if [ "$GIT_REF" = "main" ]; then
    git checkout --quiet main
    git reset --hard --quiet origin/main
else
    git checkout --quiet --detach "refs/tags/${GIT_REF}"
fi
echo "     at $(git rev-parse --short HEAD) -- $(git log -1 --pretty=%s)"

# The rest is exactly the by-hand deploy. </dev/null: this heredoc is ssh's stdin, and nothing
# below may read from it (docker compose run would otherwise swallow the rest of the script).
IMAGE_TAG="$IMAGE_TAG" COMPOSE_FILE="$COMPOSE_FILE" BASE_DOMAIN="$BASE_DOMAIN" SKIP_BACKUP="$SKIP_BACKUP" \
    ./deploy/deploy-local.sh </dev/null
REMOTE

say "Production is running ${WHAT}."
