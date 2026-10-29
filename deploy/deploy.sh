#!/usr/bin/env bash
# Deploy a RosterChief release to one or more bare-metal servers, from your own machine.
#
#   deploy/deploy.sh --master prod1.example.com                              # latest release, one server
#   deploy/deploy.sh --version v1.2.0 --master prod1 --web-node prod2 --web-node prod3
#   deploy/deploy.sh --version v1.1.9 --master prod1                          # roll back to an exact release
#   SKIP_BACKUP=1 / NO_MAINTENANCE=1 / REOPEN_MAINTENANCE=1 deploy/deploy.sh ...
#
# The release is the tarball .github/workflows/build-and-push.yml attaches to every GitHub release
# (rosterchief-vX.Y.Z.tar.gz + .sha256): code, compiled CSS, collected static files and translations, built
# once in CI. Servers need no node, no git checkout and no build step -- only `uv sync` for the virtualenv.
# It is downloaded HERE (with `gh`, so a private repo works) and copied to each server, so no server holds a
# GitHub credential.
#
# What happens, in order:
#   1. download + checksum-verify the tarball (or use --tarball for a local file);
#   2. every server: unpack into /opt/rosterchief/releases/<version>, link the shared media dirs, `uv sync`.
#      The running version is untouched -- nothing here takes the site down;
#   3. master: put the platform in maintenance mode, back up the database, `migrate`, `check --deploy`,
#      install the default Celery beat schedule (idempotent: existing schedules are never overwritten);
#   4. every server (master first, then each web node one by one): switch the `current` symlink and restart
#      web + worker (+ beat on the master), waiting for /healthz before the next node;
#   5. verify https://$BASE_DOMAIN/healthz, then ask whether to leave maintenance mode (Enter = yes).
# A failure after step 3 leaves maintenance ON and prints how to go back -- a half-upgraded platform never
# reopens by itself.
#
# Where the servers are comes from flags or the environment (keep it in a gitignored file, see
# deploy/.env.deploy.example): MASTER_HOST, WEB_NODE_HOSTS (space separated), SSH_USER, SSH_KEY_FILE,
# BASE_DOMAIN, GITHUB_REPO. SSH_USER must be root or have passwordless sudo.
set -Eeuo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
step() { printf '\033[1;34m  ->\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

MASTER="${MASTER_HOST:-}"
read -r -a WEB_NODES <<<"${WEB_NODE_HOSTS:-}"
SSH_USER="${SSH_USER:-root}"
SSH_KEY_FILE="${SSH_KEY_FILE:-}"
BASE_DOMAIN="${BASE_DOMAIN:-rosterchief.app}"
GITHUB_REPO="${GITHUB_REPO:-bsiebens/RosterChief}"
SKIP_BACKUP="${SKIP_BACKUP:-0}"
NO_MAINTENANCE="${NO_MAINTENANCE:-0}"
REOPEN_MAINTENANCE="${REOPEN_MAINTENANCE:-0}"
MAINTENANCE_MESSAGE="${MAINTENANCE_MESSAGE:-RosterChief is being upgraded. It will be back shortly.}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
VERSION="" TARBALL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:?--version needs a tag, e.g. v1.2.0}"; shift 2 ;;
        --master) MASTER="${2:?}"; shift 2 ;;
        --web-node) WEB_NODES+=("${2:?}"); shift 2 ;;
        --user) SSH_USER="${2:?}"; shift 2 ;;
        --key) SSH_KEY_FILE="${2:?}"; shift 2 ;;
        --tarball) TARBALL="${2:?}"; shift 2 ;;
        --reopen) REOPEN_MAINTENANCE=1; shift ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) die "Unknown argument '$1' (see --help)." ;;
    esac
done
[ -n "$MASTER" ] || die "No master server: pass --master HOST (or set MASTER_HOST)."

SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)
[ -z "$SSH_KEY_FILE" ] || SSH_OPTS+=(-i "$SSH_KEY_FILE" -o IdentitiesOnly=yes)
SUDO=""; [ "$SSH_USER" = root ] || SUDO="sudo -n "
ALL_HOSTS=("$MASTER" ${WEB_NODES[@]+"${WEB_NODES[@]}"})

# run HOST [args...] < script: run the script on HOST as root, args as $1...
run() {
    local host="$1"; shift
    ssh "${SSH_OPTS[@]}" "${SSH_USER}@${host}" "${SUDO}env BASE_DOMAIN=$(printf '%q' "$BASE_DOMAIN") KEEP=${KEEP_RELEASES} bash -s -- $(printf '%q ' "$@")"
}

# --- 1. the release ---------------------------------------------------------
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
if [ -z "$TARBALL" ]; then
    command -v gh >/dev/null || die "Install the GitHub CLI (gh) and log in, or pass --tarball."
    if [ -z "$VERSION" ]; then
        VERSION="$(gh release view --repo "$GITHUB_REPO" --json tagName --jq .tagName)"
        say "Latest release is ${VERSION}"
    fi
    [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--version must look like v1.2.3, got '$VERSION'."
    say "Downloading rosterchief-${VERSION}.tar.gz"
    gh release download "$VERSION" --repo "$GITHUB_REPO" --dir "$WORK" --pattern "rosterchief-${VERSION}.tar.gz*"
    TARBALL="$WORK/rosterchief-${VERSION}.tar.gz"
    ( cd "$WORK" && shasum -a 256 -c "rosterchief-${VERSION}.tar.gz.sha256" ) || die "Checksum mismatch -- refusing to deploy."
else
    [ -f "$TARBALL" ] || die "$TARBALL not found."
    [ -n "$VERSION" ] || VERSION="$(basename "$TARBALL" .tar.gz | sed 's/^rosterchief-//')"
fi
RELEASE="/opt/rosterchief/releases/${VERSION}"
say "Deploying ${VERSION} to master ${MASTER}${WEB_NODES[*]:+ and web node(s) ${WEB_NODES[*]}}"

# --- 2. every server: unpack and build the virtualenv ------------------------
for host in "${ALL_HOSTS[@]}"; do
    step "${host}: uploading and unpacking"
    scp "${SSH_OPTS[@]}" -q "$TARBALL" "${SSH_USER}@${host}:/tmp/rosterchief-${VERSION}.tar.gz"
    run "$host" "$VERSION" <<'REMOTE'
set -Eeuo pipefail
VERSION="$1"; RELEASE="/opt/rosterchief/releases/$VERSION"
[ -x /usr/local/bin/uv ] && [ -d /opt/rosterchief/shared ] || { echo "This server is not prepared: run deploy/prep-server.sh first." >&2; exit 1; }
rm -rf "$RELEASE.partial"; mkdir -p "$RELEASE.partial"
tar -xzf "/tmp/rosterchief-$VERSION.tar.gz" -C "$RELEASE.partial"
rm -f "/tmp/rosterchief-$VERSION.tar.gz"
# Uploads live outside the release so they survive every deploy.
rm -rf "$RELEASE.partial/media" "$RELEASE.partial/private_media"
ln -s /opt/rosterchief/shared/media "$RELEASE.partial/media"
ln -s /opt/rosterchief/shared/private_media "$RELEASE.partial/private_media"
# Code is root-owned and read-only to the app user: a compromised worker cannot rewrite what it runs.
( cd "$RELEASE.partial" && UV_PYTHON_INSTALL_DIR=/opt/rosterchief/python UV_CACHE_DIR=/var/cache/uv UV_COMPILE_BYTECODE=1 \
    /usr/local/bin/uv sync --frozen --no-dev --python 3.14 >/dev/null )
chmod -R a+rX "$RELEASE.partial"
rm -rf "$RELEASE"; mv "$RELEASE.partial" "$RELEASE"
REMOTE
done

# --- 3. master: maintenance, backup, migrate ---------------------------------
step "${MASTER}: checking the env file"
run "$MASTER" <<'REMOTE'
set -Eeuo pipefail
for key in DJANGO_SECRET_KEY MFA_ENCRYPTION_KEY DJANGO_DATABASE_URL DJANGO_REDIS_URL; do
    grep -Eq "^${key}=.+" /etc/rosterchief/rosterchief.env || { echo "$key is missing or empty in /etc/rosterchief/rosterchief.env" >&2; exit 1; }
done
REMOTE

MAINTENANCE_STARTED=0
maint() { # start|stop
    run "$MASTER" "$1" "$MAINTENANCE_MESSAGE" <<'REMOTE'
set -Eeuo pipefail
if [ "$1" = start ]; then
    printf '%s' "$2" | rosterchief-manage shell -c "import sys; from features.models import Maintenance; Maintenance.start(message=sys.stdin.read())"
else
    rosterchief-manage shell -c "from features.models import Maintenance; Maintenance.stop()" </dev/null
fi
REMOTE
}
reopen_hint() { echo "  ssh ${SSH_USER}@${MASTER} ${SUDO}rosterchief-manage shell -c \"'from features.models import Maintenance; Maintenance.stop()'\""; }
on_exit() {
    local status=$?
    if [ "$status" -ne 0 ] && [ "$MAINTENANCE_STARTED" = 1 ]; then
        printf '\033[1;33mThe deploy failed and the platform is STILL IN MAINTENANCE MODE.\033[0m\n' >&2
        echo "Previous releases stay on each server under /opt/rosterchief/releases; to go back, deploy one (--version vX.Y.Z)." >&2
        echo "Once it is sorted out, reopen from the control panel, or with:" >&2
        reopen_hint >&2
    fi
}
trap 'on_exit; rm -rf "$WORK"' EXIT

HAS_CURRENT="$(run "$MASTER" <<'REMOTE'
[ -x /opt/rosterchief/current/.venv/bin/python ] && echo 1 || echo 0
REMOTE
)"
if [ "$NO_MAINTENANCE" = 1 ]; then
    say "Not entering maintenance mode (NO_MAINTENANCE=1)"
elif [ "$HAS_CURRENT" = 1 ]; then
    say "Entering maintenance mode"
    maint start || die "Could not enter maintenance mode -- nothing has been changed."
    MAINTENANCE_STARTED=1
else
    say "No running release on the master (first deploy?) -- skipping maintenance mode"
fi

if [ "$HAS_CURRENT" = 0 ]; then
    say "First deploy -- skipping the pre-migration backup"
elif [ "$SKIP_BACKUP" = 1 ]; then
    say "Skipping the backup (SKIP_BACKUP=1) -- not recommended before a migration"
else
    say "Backing up the database before migrating"
    run "$MASTER" "$RELEASE" <<'REMOTE'
set -Eeuo pipefail
cd "$1" && BARE_METAL=1 ./deploy/backup.sh /var/backups/rosterchief
REMOTE
fi

say "Migrating"
run "$MASTER" "$RELEASE" <<'REMOTE'
set -Eeuo pipefail
rosterchief-manage --release "$1" migrate --noinput
rosterchief-manage --release "$1" check --deploy --fail-level ERROR
# Default schedule for any task the release adds; existing (possibly edited) schedules are left alone.
rosterchief-manage --release "$1" install_tasks
REMOTE

# --- 4. switch and restart, master first --------------------------------------
switch_host() { # HOST ROLE(master|web)
    local host="$1" role="$2"
    step "${host}: switching to ${VERSION} and restarting"
    run "$host" "$VERSION" "$role" <<'REMOTE'
set -Eeuo pipefail
VERSION="$1"; ROLE="$2"; RELEASE="/opt/rosterchief/releases/$VERSION"
PREVIOUS="$(readlink -f /opt/rosterchief/current 2>/dev/null || true)"
[ -z "$PREVIOUS" ] || echo "     previous release: $PREVIOUS"
ln -sfn "$RELEASE" /opt/rosterchief/current.new && mv -T /opt/rosterchief/current.new /opt/rosterchief/current
# Pick up unit/Caddyfile changes shipped with this release.
changed=0
for unit in rosterchief-web rosterchief-worker rosterchief-beat rosterchief-backup; do
    [ -f "$RELEASE/deploy/systemd/$unit.service" ] || continue
    cmp -s "$RELEASE/deploy/systemd/$unit.service" "/etc/systemd/system/$unit.service" || { install -m 644 "$RELEASE/deploy/systemd/$unit.service" "/etc/systemd/system/$unit.service"; changed=1; }
done
[ "$changed" = 0 ] || systemctl daemon-reload
systemctl restart rosterchief-web
for attempt in $(seq 1 20); do
    curl -fsS -H "Host: ${BASE_DOMAIN:-localhost}" -H "X-Forwarded-Proto: https" "http://127.0.0.1:8000/healthz" >/dev/null 2>&1 && { echo "     web healthy after $attempt check(s)"; break; }
    [ "$attempt" -lt 20 ] || { journalctl -u rosterchief-web -n 30 --no-pager >&2; echo "web never became healthy" >&2; exit 1; }
    sleep 2
done
systemctl restart rosterchief-worker
if [ "$ROLE" = master ] && systemctl is-enabled --quiet rosterchief-beat; then systemctl restart rosterchief-beat; fi
if [ "$ROLE" = master ] && systemctl is-enabled --quiet caddy 2>/dev/null; then
    cmp -s "$RELEASE/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile || install -o root -g caddy -m 640 "$RELEASE/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile
    systemctl is-active --quiet caddy && systemctl reload caddy || systemctl start caddy
fi
# Keep the newest releases (and whatever is current), delete the rest.
ls -1dt /opt/rosterchief/releases/v* 2>/dev/null | tail -n +"$((KEEP + 1))" | grep -vxF "$RELEASE" | xargs -r rm -rf
REMOTE
}
switch_host "$MASTER" master
for host in ${WEB_NODES[@]+"${WEB_NODES[@]}"}; do switch_host "$host" web; done

# --- 5. verify and reopen ----------------------------------------------------
say "Waiting for https://${BASE_DOMAIN}/healthz"
healthy=0
for attempt in $(seq 1 20); do
    if curl -fsS "https://${BASE_DOMAIN}/healthz" >/dev/null 2>&1; then say "Healthy after ${attempt} check(s)."; healthy=1; break; fi
    sleep 3
done
if [ "$healthy" != 1 ]; then
    run "$MASTER" <<'REMOTE' >&2 || true
journalctl -u rosterchief-web -u caddy -n 40 --no-pager
REMOTE
    die "The public health check never passed against https://${BASE_DOMAIN}/healthz."
fi

if [ "$MAINTENANCE_STARTED" = 1 ]; then
    say "Everything is up on ${VERSION}. The platform is still in maintenance mode."
    reopen=""
    if [ "$REOPEN_MAINTENANCE" = 1 ]; then
        reopen=1
    elif [ -t 0 ]; then
        while true; do
            printf '\033[1;33m?\033[0m Take the platform out of maintenance mode now? [Y/n] '
            read -r answer || break
            case "$answer" in
                ""|[Yy]|[Yy][Ee][Ss]) reopen=1; break ;;
                [Nn]|[Nn][Oo]) break ;;
                *) echo "Please answer y or n." ;;
            esac
        done
    fi
    if [ -n "$reopen" ]; then
        maint stop
        MAINTENANCE_STARTED=0
        say "Maintenance mode is off -- the platform is open."
    else
        say "Leaving the platform in maintenance mode. Reopen it from the control panel, or with:"
        reopen_hint
        MAINTENANCE_STARTED=0
    fi
fi

say "Production is running ${VERSION}."
