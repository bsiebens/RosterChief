#!/usr/bin/env bash
# Deploy production FROM the server itself -- no SSH indirection. For when you're already
# logged in and want to run the deploy directly, instead of deploy-prod.sh's ssh-from-your-Mac
# model. Run it from the repo root.
#
#   deploy/deploy-local.sh                          # deploy IMAGE_TAG=main (the default)
#   IMAGE_TAG=main-a1b2c3d deploy/deploy-local.sh    # pin/roll back to an exact build
#   SKIP_BACKUP=1 deploy/deploy-local.sh             # skip the pre-migration backup (not recommended)
#
# Assumes the checkout is already where you want it -- `git pull`/`git fetch && reset --hard`
# yourself first; this script only handles the docker/app side, same steps deploy-prod.sh's
# remote half runs, minus the git sync and the ssh wrapping around it:
#
#   ensure db/redis/caddy are up (caddy doesn't need a code deploy, so nothing else here would
#   otherwise ever start it -- this is exactly the gap that bit the first manual deploy) ->
#   back up the database -> pull the image -> migrate -> restart web + live_score_poller ->
#   verify https://$BASE_DOMAIN/healthz. No worker/beat -- scheduled jobs run via host cron
#   calling `manage.py <job>` directly (see DEPLOYMENT.md's "Scheduled jobs"). live_score_poller
#   is the one exception, a persistent process on the same image as web (see DEPLOYMENT.md's
#   "Long-running processes") -- it gets cycled onto the new image right alongside web, or a
#   deploy would silently leave it running the old code indefinitely.
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
IMAGE_TAG="${IMAGE_TAG:-main}"
BASE_DOMAIN="${BASE_DOMAIN:-rosterchief.app}"
SKIP_BACKUP="${SKIP_BACKUP:-0}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -d .git ]             || die "Run this from the repo root (no .git here)."
[ -f "$COMPOSE_FILE" ]  || die "$COMPOSE_FILE not found -- wrong directory, or wrong COMPOSE_FILE?"
[ -f .env.production ]  || die ".env.production missing (Django config). Copy from .env.production.example."
[ -f .env ]             || die ".env missing (compose vars: POSTGRES_PASSWORD, ...). Copy from .env.compose.example."

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

say "Deploying image tag '${IMAGE_TAG}' — checkout at $(git rev-parse --short HEAD) ($(git log -1 --pretty=%s))"

# db/redis/caddy are all restart:unless-stopped and should already be up from steady state --
# this is belt-and-suspenders (e.g. right after a host reboot, or a first deploy) before
# backup.sh execs into db/web and before the healthz check below relies on caddy being there.
say "Ensuring db + redis + caddy are up"
dc up -d db redis caddy

if [ "$SKIP_BACKUP" = "1" ]; then
    say "Skipping backup (SKIP_BACKUP=1) -- not recommended before a migration"
else
    say "Backing up the database before migrating"
    COMPOSE="docker compose -f $COMPOSE_FILE" ./deploy/backup.sh /var/backups/rosterchief
fi

say "Pulling image tag '${IMAGE_TAG}'"
IMAGE_TAG="$IMAGE_TAG" dc pull web live_score_poller

say "Running migrations"
dc run --rm web python manage.py migrate --noinput

say "Restarting web + live_score_poller"
IMAGE_TAG="$IMAGE_TAG" dc up -d --no-deps web live_score_poller

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
dc logs --tail 40 web >&2
exit 1
