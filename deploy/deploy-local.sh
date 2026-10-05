#!/usr/bin/env bash
# Deploy production FROM the server itself. deploy/deploy-prod.sh runs exactly this script over
# SSH from your own machine (after updating the checkout), so the two can never drift apart;
# run it by hand only when you're logged in anyway. Run it from the repo root.
#
#   deploy/deploy-local.sh                          # deploy IMAGE_TAG=main (the default)
#   IMAGE_TAG=main-a1b2c3d deploy/deploy-local.sh    # pin/roll back to an exact build
#   SKIP_BACKUP=1 deploy/deploy-local.sh             # skip the pre-migration backup (not recommended)
#   NO_MAINTENANCE=1 deploy/deploy-local.sh          # don't wrap the deploy in maintenance mode
#   MAINTENANCE_MESSAGE="..." deploy/deploy-local.sh # what clubs see while it is closed
#
# The platform is put in maintenance mode (features.models.Maintenance) before anything is
# backed up or migrated, and once the deploy is healthy the script asks whether to reopen it (Enter = yes).
# That prompt needs a terminal: deploy-prod.sh gives this script one (ssh -t). With no terminal
# -- or if the deploy fails -- maintenance is deliberately LEFT ON and the command to end it is
# printed, so a half-upgraded platform never reopens by itself.
#
# Assumes the checkout is already where you want it -- `git pull`/`git fetch && reset --hard`
# yourself first; this script only handles the docker/app side, same steps deploy-prod.sh's
# remote half runs, minus the git sync and the ssh wrapping around it:
#
#   ensure db/redis/caddy are up (caddy doesn't need a code deploy, so nothing else here would
#   otherwise ever start it -- this is exactly the gap that bit the first manual deploy) ->
#   start maintenance mode -> back up the database -> pull the image -> migrate -> restart web
#   -> verify https://$BASE_DOMAIN/healthz -> ask, then end maintenance mode. No worker/beat, and no separate scheduler/poller container
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
BACKUP_DIR="${BACKUP_DIR:-/var/backups/rosterchief}"
NO_MAINTENANCE="${NO_MAINTENANCE:-0}"
MAINTENANCE_MESSAGE="${MAINTENANCE_MESSAGE:-RosterChief is being upgraded. It will be back shortly.}"
# Every docker compose call below uses this image tag -- pull, migrate and the restart alike.
export IMAGE_TAG

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -d .git ]             || die "Run this from the repo root (no .git here)."
[ -f "$COMPOSE_FILE" ]  || die "$COMPOSE_FILE not found -- wrong directory, or wrong COMPOSE_FILE?"
[ -f .env.production ]  || die ".env.production missing (Django config). Copy from .env.production.example."
[ -f .env ]             || die ".env missing (compose vars: POSTGRES_PASSWORD, ...). Copy from .env.compose.example."

# Keys the code refuses to run without -- checked before anything is stopped, backed up or
# migrated, so a missing one fails here with a clear message instead of halfway through.
# (Only whether the line has a value; the values themselves are never printed.)
# The value after KEY=, without a trailing " # comment" or surrounding quotes/whitespace (an
# example file's "KEY=   # how to generate it" line counts as empty, as it is for Django).
env_value() {
    sed -n "s/^$2=//p" "$1" | tail -1 \
        | sed -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//'
}
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
# db and redis first. caddy is started with --no-deps: it depends on web, and a plain
# `up caddy` would recreate web on the new image before the backup and migration below.
say "Ensuring db + redis are up"
dc up -d --remove-orphans db redis

# The Caddyfile is bind-mounted as a single file, and `git reset` replaces that file, so a
# running caddy keeps serving the old config. Recreate it only when the config actually changed.
if [ -n "$(dc ps -q caddy 2>/dev/null)" ] && ! dc exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null | cmp -s - deploy/caddy/Caddyfile; then
    say "Caddyfile changed -- recreating caddy"
    dc up -d --no-deps --force-recreate caddy
else
    say "Ensuring caddy is up"
    dc up -d --no-deps caddy
fi

# Close the platform before anything is backed up or migrated. Needs a running web (first
# deploys have nothing to close, and no Maintenance table yet). Runs against the OLD image on
# purpose: the lock-down is in place before the new code or schema is. Maintenance is stored in
# the database and shared Redis, so it survives the web restart below.
MAINTENANCE_STARTED=0
maintenance_command() {
    echo "docker compose -f $COMPOSE_FILE exec -T web python manage.py shell -c \"from features.models import Maintenance; Maintenance.stop()\""
}
if [ "$NO_MAINTENANCE" = "1" ]; then
    say "Not entering maintenance mode (NO_MAINTENANCE=1)"
elif [ -z "$(dc ps -q --status running web 2>/dev/null)" ]; then
    say "web is not running (first deploy?) -- nothing to put in maintenance mode"
else
    say "Entering maintenance mode"
    dc exec -T -e MAINTENANCE_MESSAGE="$MAINTENANCE_MESSAGE" web python manage.py shell -c "import os; from features.models import Maintenance; Maintenance.start(message=os.environ['MAINTENANCE_MESSAGE'])" </dev/null \
        || die "Could not enter maintenance mode -- nothing has been changed."
    MAINTENANCE_STARTED=1
fi
# Any exit before the platform is deliberately reopened leaves it closed, on purpose.
on_exit() {
    local status=$?
    if [ "$status" -ne 0 ] && [ "$MAINTENANCE_STARTED" = "1" ]; then
        printf '\033[1;33mThe deploy failed and the platform is STILL IN MAINTENANCE MODE.\033[0m\n' >&2
        echo "Once it is sorted out, reopen it from the control panel, or with:" >&2
        echo "  $(maintenance_command)" >&2
    fi
}
trap on_exit EXIT

if [ -z "$(dc ps -q --status running web 2>/dev/null)" ]; then
    # backup.sh reads the uploads out of the running web container. Not running means a first
    # deploy (nothing to back up yet) or a stopped app -- say so rather than fail.
    say "web is not running (first deploy?) -- skipping the pre-migration backup"
elif [ "$SKIP_BACKUP" = "1" ]; then
    say "Skipping backup (SKIP_BACKUP=1) -- not recommended before a migration"
else
    say "Backing up the database before migrating"
    COMPOSE="docker compose -f $COMPOSE_FILE" ./deploy/backup.sh "$BACKUP_DIR"
fi

say "Pulling image tag '${IMAGE_TAG}'"
dc pull web

say "Running migrations"
# -T and </dev/null: no TTY and no stdin -- identical by hand and over deploy-prod.sh's SSH.
dc run --rm -T web python manage.py migrate --noinput </dev/null

say "Running deployment checks"
# The new image against this server's real .env.production: blocks on errors (e.g. a
# placeholder DJANGO_SECRET_KEY, rosterchief.E001) so the old web keeps serving, but only
# reports warnings -- CI already runs the same checks at --fail-level WARNING.
dc run --rm -T web python manage.py check --deploy --fail-level ERROR </dev/null

say "Restarting web"
dc up -d --no-deps --remove-orphans web

# From here on web runs this tag; record it in .env so a by-hand `docker compose run ... web`
# (one-off management commands) uses the same image instead of compose.yaml's :main fallback.
say "Recording IMAGE_TAG=${IMAGE_TAG} in .env"
./deploy/record-image-tag.sh "$IMAGE_TAG"

say "Waiting for https://${BASE_DOMAIN}/healthz"
healthy=0
for attempt in $(seq 1 20); do
    if curl -fsS "https://${BASE_DOMAIN}/healthz" >/dev/null 2>&1; then
        say "Healthy after ${attempt} check(s)."
        healthy=1
        break
    fi
    sleep 3
done

if [ "$healthy" != "1" ]; then
    echo "ERROR: health check never passed against https://${BASE_DOMAIN}/healthz" >&2
    echo "Recent web logs:" >&2
    dc logs --tail 40 web >&2
    exit 1
fi

if [ "$MAINTENANCE_STARTED" = "1" ]; then
    say "Everything is up on '${IMAGE_TAG}'. The platform is still in maintenance mode."
    # Enter means yes: everything is up and this is the question the deploy was waiting on.
    # Asked until it gets a clear answer; with no terminal to ask on, the platform stays closed.
    reopen=""
    if ( : </dev/tty ) 2>/dev/null; then
        while true; do
            printf '\033[1;33m?\033[0m Take the platform out of maintenance mode now? [Y/n] ' >/dev/tty
            read -r answer </dev/tty || break
            case "$answer" in
                ""|[Yy]|[Yy][Ee][Ss]) reopen=1; break ;;
                [Nn]|[Nn][Oo]) break ;;
                *) echo "Please answer y or n." >/dev/tty ;;
            esac
        done
    fi
    if [ -n "$reopen" ]; then
        say "Leaving maintenance mode"
        dc exec -T web python manage.py shell -c "from features.models import Maintenance; Maintenance.stop()" </dev/null
        MAINTENANCE_STARTED=0
        say "Maintenance mode is off -- the platform is open."
    else
        say "Leaving the platform in maintenance mode. Reopen it from the control panel, or with:"
        echo "  $(maintenance_command)"
    fi
fi
