#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: sudo $0 release CHECKOUT GIT_REF" >&2
    echo "       sudo $0 rollback RELEASE_DIRECTORY" >&2
    exit 2
}
[[ $EUID == 0 ]] || usage
case "${1:-}" in
    release) [[ $# == 3 ]] || usage ;;
    rollback) [[ $# == 2 ]] || usage ;;
    *) usage ;;
esac

app_dir=/opt/rosterchief
config_dir=/etc/rosterchief
data_dir=/var/lib/rosterchief
units=(rosterchief-web.service rosterchief-management.service rosterchief-controlpanel.service rosterchief-worker.service rosterchief-beat.service)
if [[ ! -r $config_dir/site.env || ! -r $config_dir/app.env ]]; then
    echo "Run bootstrap.sh before deploying." >&2
    exit 2
fi
source "$config_dir/site.env"

exec 9>/run/lock/rosterchief-deploy.lock
if ! flock -n 9; then
    echo "A RosterChief deploy or rollback is already running." >&2
    exit 1
fi

activate() {
    local target=$1
    ln -sfnT "$target" "$app_dir/.next"
    mv -fT "$app_dir/.next" "$app_dir/current"
}

wait_for_app() {
    local attempt
    for attempt in {1..15}; do
        if /usr/local/sbin/rosterchief-health; then
            return 0
        fi
        sleep 2
    done
    return 1
}

start_app() {
    systemctl start "${units[@]}" && wait_for_app
}

if [[ $1 == rollback ]]; then
    release=$(realpath -e -- "$2")
    if [[ $(dirname -- "$release") != "$app_dir/releases" || ! -f $release/.ready ]]; then
        echo "Choose a completed release directory in $app_dir/releases." >&2
        exit 2
    fi
    previous=$(readlink -f "$app_dir/current" || true)
    systemctl stop "${units[@]}"
    activate "$release"
    if ! start_app; then
        systemctl stop "${units[@]}"
        if [[ -n $previous && -d $previous ]]; then
            activate "$previous"
            start_app || true
        fi
        echo "Rollback health check failed. See the RosterChief service journals." >&2
        exit 1
    fi
    echo "Running $release"
    exit 0
fi

checkout=$(realpath -e -- "$2")
git_source() { git -c "safe.directory=$checkout" -C "$checkout" "$@"; }
revision=$(git_source rev-parse --verify --end-of-options "$3^{commit}")
release=$(mktemp -d "$app_dir/releases/${revision:0:12}.XXXXXXXX")
chmod 0755 "$release"
git_source archive --format=tar "$revision" | tar --extract --file=- --directory="$release" --no-same-owner --no-same-permissions
printf '%s\n' "$revision" > "$release/.revision"

chown -hR rosterchief-build:rosterchief-build "$release"
build_env=(env "PATH=$app_dir/bin:/usr/local/bin:/usr/bin:/bin"
    "UV_PYTHON_INSTALL_DIR=$app_dir/python" "UV_CACHE_DIR=$data_dir/build/uv"
    "npm_config_cache=$data_dir/build/npm")
build_as() { runuser -u rosterchief-build -- "${build_env[@]}" "$@"; }
(
    cd "$release"
    build_as uv sync --locked --no-dev --python 3.14
)
for frontend in management controlpanel; do
    (
        cd "$release/frontend/$frontend"
        build_as npm-24 ci --include=dev --no-audit --no-fund
        build_as npm-24 run build
    )
done
chown -hR root:root "$release"

django() {
    systemd-run --quiet --wait --pipe --collect \
        -p User=rosterchief -p Group=rosterchief \
        -p "WorkingDirectory=$release" -p "EnvironmentFile=$config_dir/app.env" \
        "$release/.venv/bin/python" "$release/manage.py" "$@"
}
install -d -m 0755 -o rosterchief -g rosterchief "$release/staticfiles"
django check --deploy --fail-level ERROR
django collectstatic --noinput
chown -hR root:root "$release/staticfiles"

previous=$(readlink -f "$app_dir/current" || true)
if [[ -n $previous && -f $previous/.ready ]]; then
    systemctl stop "${units[@]}"
fi

# This local peer-auth checkpoint includes public and all club schemas.
checkpoint=$(mktemp "$data_dir/checkpoints/$(date -u +%Y%m%dT%H%M%SZ)-${revision:0:12}.XXXXXXXX")
if ! runuser -u rosterchief -- pg_dump --format=custom --no-password --dbname "${ROSTERCHIEF_DATABASE_NAME:-rosterchief}" > "$checkpoint"; then
    if [[ -n $previous && -f $previous/.ready ]]; then
        start_app || true
    fi
    echo "PostgreSQL checkpoint failed. No migrations ran." >&2
    exit 1
fi
echo "Pre-migration PostgreSQL checkpoint at $checkpoint"

if ! django migrate_schemas --noinput; then
    echo "Database setup failed. Check the schema before restarting services. Checkpoint: $checkpoint" >&2
    exit 1
fi
if [[ ! -f $previous/.ready ]] && ! django bootstrap_platform --owner-email "$PLATFORM_OWNER_EMAIL"; then
    echo "Platform setup failed. Checkpoint: $checkpoint" >&2
    exit 1
fi

activate "$release"
if ! start_app; then
    systemctl stop "${units[@]}"
    if [[ -n $previous && -f $previous/.ready ]]; then
        activate "$previous"
    fi
    echo "New release failed its application health checks. Checkpoint: $checkpoint. Previous release: $previous" >&2
    exit 1
fi
touch "$release/.ready"
systemctl enable "${units[@]}"
systemctl enable --now rosterchief-caddy.service rosterchief-health.timer
echo "Running $release. Check Caddy certificate issuance and the public routes with rosterchief-health --tls."
