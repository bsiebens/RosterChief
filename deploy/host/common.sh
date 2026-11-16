# shellcheck shell=bash disable=SC2034
# Shared by bootstrap.sh, deploy.sh, health.sh and cloudflare-ips.sh. Sourced, never executed.
# Runs on the servers (Amazon Linux 2023, bash 5).

RC_APP=/opt/rosterchief
RC_CONFIG=/etc/rosterchief
RC_DATA=/var/lib/rosterchief
RC_LIB=/usr/local/lib/rosterchief

rc_has_backend() { [[ $1 == full || $1 == backend ]]; }
rc_has_frontend() { [[ $1 == full || $1 == frontend ]]; }
rc_valid_role() { [[ $1 == full || $1 == backend || $1 == frontend ]]; }

# The application units (the ones stopped and started around a release) for a role.
rc_app_units() {
    local role=$1
    if rc_has_backend "$role"; then
        printf '%s\n' rosterchief-web.service rosterchief-worker.service rosterchief-beat.service
    fi
    if rc_has_frontend "$role"; then
        printf '%s\n' rosterchief-management.service rosterchief-controlpanel.service
    fi
}

# Is a systemd unit file from deploy/host/systemd meant for this role?
rc_unit_wanted() {
    local role=$1 name=$2
    case $name in
        rosterchief-web.service | rosterchief-worker.service | rosterchief-beat.service) rc_has_backend "$role" ;;
        rosterchief-management.service | rosterchief-controlpanel.service) rc_has_frontend "$role" ;;
        rosterchief-cloudflare-ips.*) rc_has_frontend "$role" ;;
        *) return 0 ;;
    esac
}

# Set KEY=VALUE in an env file, replacing an existing line and keeping the file's mode.
rc_set_kv() {
    local file=$1 key=$2 value=$3 tmp
    tmp=$(mktemp "$file.XXXXXX")
    if [[ -f $file ]]; then
        grep -v "^${key}=" "$file" > "$tmp" || true
        chmod --reference="$file" "$tmp"
    else
        chmod 0600 "$tmp"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv -f "$tmp" "$file"
}

# Run caddy validate with the same environment the service gets.
rc_validate_caddyfile() {
    (
        set -a
        # shellcheck disable=SC1091
        source "$RC_CONFIG/site.env"
        [[ -r $RC_CONFIG/caddy.env ]] && source "$RC_CONFIG/caddy.env"
        set +a
        /usr/local/bin/caddy validate --config "$1" --adapter caddyfile > /dev/null
    )
}

# Install scripts, systemd units and the Caddyfile from an extracted deploy/host directory.
# Called by bootstrap.sh and by every release, so host-side changes ship with the version.
rc_install_host_files() {
    local src=$1 role=$2 unit name
    install -d -m 0755 "$RC_LIB"
    install -m 0644 "$src/common.sh" "$RC_LIB/common.sh"
    for name in bootstrap deploy health cloudflare-ips; do
        install -m 0755 "$src/$name.sh" "$RC_LIB/$name.sh"
    done
    ln -sfn "$RC_LIB/deploy.sh" /usr/local/sbin/rosterchief-deploy
    ln -sfn "$RC_LIB/health.sh" /usr/local/sbin/rosterchief-health
    ln -sfn "$RC_LIB/cloudflare-ips.sh" /usr/local/sbin/rosterchief-cloudflare-ips
    for unit in "$src"/systemd/*; do
        name=$(basename "$unit")
        if rc_unit_wanted "$role" "$name"; then
            install -m 0644 "$unit" "/etc/systemd/system/$name"
        fi
    done
    install -m 0644 "$src/Caddyfile.$role" "$RC_CONFIG/Caddyfile"
    systemctl daemon-reload
}
