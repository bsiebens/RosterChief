#!/usr/bin/env bash
# Installs a released version on this host, according to its role (full, backend or frontend).
#
#   rosterchief-deploy release vX.Y.Z [--from-dir DIR]   download, verify and activate a release
#   rosterchief-deploy rollback [RELEASE_DIRECTORY]      switch to the previous (or a named) completed release
#   rosterchief-deploy status                            print role, running version and health
#
# Releases come from GitHub Releases (credentials in /etc/rosterchief/deploy.env) or, with --from-dir,
# from a directory holding the same files. Nothing is built here: the Python environment is installed from
# the locked requirements and the frontends arrive already built.
set -euo pipefail
here=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source=common.sh
source "$here/common.sh"

usage() {
    echo "Usage: sudo $0 release vX.Y.Z [--from-dir DIR]" >&2
    echo "       sudo $0 rollback [RELEASE_DIRECTORY]" >&2
    echo "       sudo $0 status" >&2
    exit 2
}
[[ $EUID == 0 ]] || usage
cmd=${1:-}
[[ -n $cmd ]] && shift
if [[ ! -r $RC_CONFIG/site.env ]]; then
    echo "Run bootstrap.sh before deploying." >&2
    exit 2
fi
# shellcheck disable=SC1091
source "$RC_CONFIG/site.env"
role=${ROSTERCHIEF_ROLE:-full}
rc_valid_role "$role" || { echo "Unknown ROSTERCHIEF_ROLE '$role' in site.env." >&2; exit 2; }
mapfile -t units < <(rc_app_units "$role")

if [[ $cmd == status ]]; then
    current=$(readlink -f "$RC_APP/current" 2> /dev/null || true)
    printf 'role=%s version=%s release=%s\n' "$role" "$(cat "$current/.version" 2> /dev/null || echo none)" "${current:-none}"
    /usr/local/sbin/rosterchief-health && echo "health=ok" || { echo "health=FAILING"; exit 1; }
    exit 0
fi

if [[ -z ${RC_REEXEC:-} ]]; then
    exec 9> /run/lock/rosterchief-deploy.lock
    if ! flock -n 9; then
        echo "A RosterChief deploy or rollback is already running." >&2
        exit 1
    fi
fi
# A re-executed copy inherits file descriptor 9, so it already holds the lock.

activate() {
    ln -sfnT "$1" "$RC_APP/.next"
    mv -fT "$RC_APP/.next" "$RC_APP/current"
}

wait_for_app() {

    for _ in {1..15}; do
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

previous_ready_release() {
    local current dir
    current=$(readlink -f "$RC_APP/current" 2> /dev/null || true)
    while IFS= read -r dir; do
        dir=${dir%/}
        if [[ -f $dir/.ready && $(readlink -f "$dir") != "$current" ]]; then
            printf '%s\n' "$dir"
            return 0
        fi
    done < <(ls -1dt "$RC_APP"/releases/*/ 2> /dev/null)
    return 1
}

if [[ $cmd == rollback ]]; then
    if [[ $# -ge 1 ]]; then
        release=$(realpath -e -- "$1")
    else
        release=$(previous_ready_release) || { echo "There is no earlier completed release to roll back to." >&2; exit 1; }
    fi
    if [[ $(dirname -- "$release") != "$RC_APP/releases" || ! -f $release/.ready ]]; then
        echo "Choose a completed release directory in $RC_APP/releases." >&2
        exit 2
    fi
    previous=$(readlink -f "$RC_APP/current" || true)
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
    echo "Running $release ($(cat "$release/.version" 2> /dev/null || echo unknown version))"
    if rc_has_backend "$role"; then
        echo "Note: database migrations are not reversed. If the rolled-back release cannot run on the migrated schema, restore a checkpoint (see deploy/README.md)."
    fi
    exit 0
fi

[[ $cmd == release ]] || usage
tag=${1:-}
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]] || usage
shift
from_dir=
if [[ ${1:-} == --from-dir ]]; then
    from_dir=$(realpath -e -- "${2:?--from-dir needs a directory}")
fi

# ---------------------------------------------------------------- fetch and verify
parts=(host)
rc_has_backend "$role" && parts+=(backend)
rc_has_frontend "$role" && parts+=(frontend)
asset() { printf 'rosterchief-%s-%s.tar.gz' "$tag" "$1"; }

if [[ -n $from_dir ]]; then
    dl=$from_dir
else
    # shellcheck disable=SC1091
    source "$RC_CONFIG/deploy.env"
    : "${GITHUB_REPO:?Set GITHUB_REPO in $RC_CONFIG/deploy.env}" "${GITHUB_TOKEN:?Set GITHUB_TOKEN in $RC_CONFIG/deploy.env}"
    dl=$RC_DATA/build/downloads/$tag
    install -d -m 0700 "$dl"
    # The token goes through a curl config on a pipe so it never appears in the process list.
    gh_curl() { curl --fail --silent --show-error --location --retry 3 -K <(printf 'header = "Authorization: Bearer %s"\n' "$GITHUB_TOKEN") "$@"; }
    gh_curl -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$GITHUB_REPO/releases/tags/$tag" --output "$dl/release.json"
    for name in SHA256SUMS "${parts[@]}"; do
        file=$name
        [[ $name != SHA256SUMS ]] && file=$(asset "$name")
        id=$(jq -r --arg n "$file" '.assets[] | select(.name == $n) | .id' "$dl/release.json")
        [[ -n $id ]] || { echo "Release $tag has no asset $file." >&2; exit 1; }
        gh_curl -H 'Accept: application/octet-stream' "https://api.github.com/repos/$GITHUB_REPO/releases/assets/$id" --output "$dl/$file"
    done
fi
for name in "${parts[@]}"; do
    file=$(asset "$name")
    line=$(grep -F "  $file" "$dl/SHA256SUMS" || true)
    [[ -n $line ]] || { echo "SHA256SUMS does not list $file." >&2; exit 1; }
    (cd "$dl" && printf '%s\n' "$line" | sha256sum --check --status) || { echo "Checksum mismatch for $file. Refusing to deploy." >&2; exit 1; }
done
echo "Verified ${parts[*]} for $tag."

# ---------------------------------------------------------------- run the host scripts shipped with this release
rm -rf "$dl/host"
tar --extract --gzip --file "$dl/$(asset host)" --directory "$dl" --no-same-owner --no-same-permissions
if [[ -z ${RC_REEXEC:-} ]] && ! cmp -s "$dl/host/deploy.sh" "$RC_LIB/deploy.sh"; then
    echo "Switching to the deploy script from $tag."
    RC_REEXEC=1 exec bash "$dl/host/deploy.sh" release "$tag" --from-dir "$dl"
fi
if [[ ! -x /usr/local/bin/caddy ]] || ! rc_validate_caddyfile "$dl/host/Caddyfile.$role"; then
    echo "The Caddyfile for this release does not validate on this host. Nothing was changed." >&2
    exit 1
fi
rc_install_host_files "$dl/host" "$role"

# ---------------------------------------------------------------- unpack and prepare
revision_dir=$RC_APP/releases
release=$(mktemp -d "$revision_dir/${tag}.XXXXXXXX")
chmod 0755 "$release"
for name in "${parts[@]}"; do
    [[ $name == host ]] && continue
    tar --extract --gzip --file "$dl/$(asset "$name")" --directory "$release" --no-same-owner --no-same-permissions
done
printf '%s\n' "$tag" > "$release/.version"

django() {
    systemd-run --quiet --wait --pipe --collect \
        -p User=rosterchief -p Group=rosterchief \
        -p "WorkingDirectory=$release" -p "EnvironmentFile=$RC_CONFIG/app.env" \
        "$release/.venv/bin/python" "$release/manage.py" "$@"
}

if rc_has_backend "$role"; then
    chown -hR rosterchief-build:rosterchief-build "$release"
    runuser -u rosterchief-build -- env "PATH=$RC_APP/bin:/usr/local/bin:/usr/bin:/bin" \
        "UV_PYTHON_INSTALL_DIR=$RC_APP/python" "UV_CACHE_DIR=$RC_DATA/build/uv" \
        bash -c 'cd "$1" && uv sync --locked --no-dev --python 3.14' _ "$release"
    chown -hR root:root "$release"
    install -d -m 0755 -o rosterchief -g rosterchief "$release/staticfiles"
    django check --deploy --fail-level ERROR
    django collectstatic --noinput
    chown -hR root:root "$release/staticfiles"
else
    chown -hR root:root "$release"
fi

# ---------------------------------------------------------------- switch over
previous=$(readlink -f "$RC_APP/current" || true)
if [[ -n $previous && -f $previous/.ready ]]; then
    systemctl stop "${units[@]}"
fi

checkpoint=
if rc_has_backend "$role"; then
    # This local peer-auth checkpoint includes public and all club schemas.
    checkpoint=$(mktemp "$RC_DATA/checkpoints/$(date -u +%Y%m%dT%H%M%SZ)-${tag}.XXXXXXXX")
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
fi

activate "$release"
if ! start_app; then
    systemctl stop "${units[@]}"
    if [[ -n $previous && -f $previous/.ready ]]; then
        activate "$previous"
    fi
    echo "New release failed its application health checks. Checkpoint: ${checkpoint:-none}. Previous release: ${previous:-none}" >&2
    exit 1
fi
touch "$release/.ready"
printf '%s\n' "$tag" >> "$RC_DATA/deployed-versions"
systemctl enable "${units[@]}"
systemctl enable --now rosterchief-caddy.service rosterchief-health.timer
if rc_has_frontend "$role"; then
    systemctl enable --now rosterchief-cloudflare-ips.timer
fi
systemctl reload-or-restart rosterchief-caddy.service

# Keep the five newest completed releases (plus the active one); drop failed attempts.
keep=5
count=0
current=$(readlink -f "$RC_APP/current")
while IFS= read -r dir; do
    dir=${dir%/}
    [[ $(readlink -f "$dir") == "$current" ]] && continue
    if [[ -f $dir/.ready ]]; then
        count=$((count + 1))
        (( count > keep - 1 )) && rm -rf -- "$dir"
    else
        rm -rf -- "$dir"
    fi
done < <(ls -1dt "$RC_APP"/releases/*/ 2> /dev/null)

echo "Running $tag from $release. Verify the public routes with: rosterchief-health --tls"
