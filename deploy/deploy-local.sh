#!/usr/bin/env bash
# Deploy production FROM the server itself. deploy/deploy-prod.sh runs exactly this script over
# SSH from your own machine (after updating the checkout), so the two can never drift apart;
# run it by hand only when you're logged in anyway. Run it from the repo root.
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
#   back up the database -> pull the image -> migrate -> restart web -> verify
#   https://$BASE_DOMAIN/healthz. No worker/beat, and no separate scheduler/poller container
#   either -- every scheduled platform job runs from an in-process APScheduler thread inside
#   web itself (see features/scheduler.py and DEPLOYMENT.md's "Scheduled jobs"), so restarting
#   web is all a deploy needs to cycle it onto the new image. `--remove-orphans` below cleans
#   up a now-orphaned `live_score_poller` container left over from before that container was
#   retired, on whichever box's first deploy after the change.
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

# Keys the code refuses to run without -- checked before anything is stopped, backed up or
# migrated, so a missing one fails here with a clear message instead of halfway through.
# (Only whether the line has a value; the values themselves are never printed.)
env_value() { sed -n "s/^$2=//p" "$1" | tail -1; }
for key in DJANGO_SECRET_KEY MFA_ENCRYPTION_KEY; do
    [ -n "$(env_value .env.production "$key")" ] \
        || die "$key is missing or empty in .env.production -- see .env.production.example and DEPLOYMENT.md."
done
if [ -z "$(env_value .env BACKUP_AGE_RECIPIENT)" ] && [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    say "Note: BACKUP_AGE_RECIPIENT is not set in .env -- the pre-migration backup will be unencrypted (see DEPLOYMENT.md, Backups)."
fi

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

say "Deploying image tag '${IMAGE_TAG}' -- checkout at $(git rev-parse --short HEAD) ($(git log -1 --pretty=%s))"

# db/redis/caddy are all restart:unless-stopped and should already be up from steady state --
# this is belt-and-suspenders (e.g. right after a host reboot, or a first deploy) before
# backup.sh execs into db/web and before the healthz check below relies on caddy being there.
say "Ensuring db + redis + caddy are up"
dc up -d --remove-orphans db redis caddy

if [ "$SKIP_BACKUP" = "1" ]; then
    say "Skipping backup (SKIP_BACKUP=1) -- not recommended before a migration"
else
    say "Backing up the database before migrating"
    COMPOSE="docker compose -f $COMPOSE_FILE" ./deploy/backup.sh /var/backups/rosterchief
fi

say "Pulling image tag '${IMAGE_TAG}'"
IMAGE_TAG="$IMAGE_TAG" dc pull web

say "Running migrations"
# -T and </dev/null: no TTY and no stdin -- identical by hand and over deploy-prod.sh's SSH.
dc run --rm -T web python manage.py migrate --noinput </dev/null

say "Restarting web"
IMAGE_TAG="$IMAGE_TAG" dc up -d --no-deps --remove-orphans web

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
