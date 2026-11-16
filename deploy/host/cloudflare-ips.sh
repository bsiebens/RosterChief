#!/usr/bin/env bash
# Controls whether Caddy trusts Cloudflare's CF-Connecting-IP header.
#
#   rosterchief-cloudflare-ips set on|off   switch the proxied mode (updates site.env)
#   rosterchief-cloudflare-ips refresh      re-fetch Cloudflare's ranges (run weekly by a timer)
#
# Caddy imports /etc/rosterchief/cloudflare-ips.caddy in its global options. When the proxy is on, that file
# trusts forwarded headers ONLY from Cloudflare's published ranges; when off it is an empty comment, so
# Caddy trusts nothing and uses the TCP peer address. A forged CF-Connecting-IP from anywhere else is ignored.
set -euo pipefail
here=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source=common.sh
source "$here/common.sh"

[[ $EUID == 0 ]] || { echo "Run as root." >&2; exit 2; }
snippet=$RC_CONFIG/cloudflare-ips.caddy
site_env=$RC_CONFIG/site.env

fetch_ranges() {
    local v4 v6
    v4=$(curl --fail --silent --show-error --max-time 20 https://www.cloudflare.com/ips-v4)
    v6=$(curl --fail --silent --show-error --max-time 20 https://www.cloudflare.com/ips-v6)
    local all line count4=0 count6=0
    all=$(printf '%s\n%s\n' "$v4" "$v6")
    while IFS= read -r line; do
        [[ -z $line ]] && continue
        [[ $line =~ ^[0-9a-fA-F:.]+/[0-9]{1,3}$ ]] || { echo "Unexpected line in Cloudflare IP list: $line" >&2; return 1; }
    done <<< "$all"
    count4=$(grep -c . <<< "$v4" || true)
    count6=$(grep -c . <<< "$v6" || true)
    if (( count4 < 5 || count6 < 1 )); then
        echo "Cloudflare IP list looks incomplete ($count4 IPv4, $count6 IPv6). Keeping the current file." >&2
        return 1
    fi
    printf '%s\n' "$all" | grep . | tr '\n' ' '
}

render() {
    case $1 in
        on)
            local ranges
            ranges=$(fetch_ranges)
            printf '# Managed by rosterchief-cloudflare-ips. Cloudflare proxy is ON.\nservers {\n\ttrusted_proxies static %s\n\tclient_ip_headers CF-Connecting-IP\n}\n' "${ranges% }"
            ;;
        *)
            printf '# Managed by rosterchief-cloudflare-ips. Cloudflare proxy is OFF: no forwarded header is trusted.\n'
            ;;
    esac
}

apply() {
    local mode=$1 tmp
    tmp=$(mktemp "$snippet.XXXXXX")
    render "$mode" > "$tmp"
    if [[ -f $snippet ]] && cmp -s "$tmp" "$snippet"; then
        rm -f "$tmp"
        return 0
    fi
    local backup=
    if [[ -f $snippet ]]; then
        backup=$(mktemp "$snippet.old.XXXXXX")
        cp -p "$snippet" "$backup"
    fi
    install -m 0644 "$tmp" "$snippet"
    rm -f "$tmp"
    if systemctl is-active --quiet rosterchief-caddy.service; then
        if ! systemctl reload rosterchief-caddy.service; then
            echo "Caddy rejected the new trusted-proxy list. Restoring the previous one." >&2
            if [[ -n $backup ]]; then install -m 0644 "$backup" "$snippet"; fi
            rm -f "$backup"
            exit 1
        fi
    fi
    rm -f "$backup"
    echo "Updated $snippet (proxy $mode)."
}

# shellcheck disable=SC1090
[[ -r $site_env ]] && source "$site_env"
case "${1:-}" in
    set)
        [[ ${2:-} == on || ${2:-} == off ]] || { echo "Usage: $0 set on|off" >&2; exit 2; }
        mode=$2
        if [[ $mode == on ]]; then rc_set_kv "$site_env" CLOUDFLARE_PROXIED true; else rc_set_kv "$site_env" CLOUDFLARE_PROXIED false; fi
        apply "$mode"
        ;;
    refresh)
        if [[ ${CLOUDFLARE_PROXIED:-true} == true ]]; then apply on; else apply off; fi
        ;;
    *)
        echo "Usage: $0 set on|off | refresh" >&2
        exit 2
        ;;
esac
