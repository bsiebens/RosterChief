#!/usr/bin/env bash
# Health checks for this host's role. Without --tls: systemd units and local HTTP probes.
# With --tls: also Caddy, the public routes over local TLS, and (frontend/full) a check that Caddy
# ignores a forged CF-Connecting-IP header from a non-Cloudflare peer.
set -euo pipefail
here=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source=common.sh
source "$here/common.sh"

if [[ -z ${ROSTERCHIEF_BASE_DOMAIN:-} ]]; then
    # shellcheck disable=SC1091
    source "$RC_CONFIG/site.env"
fi
role=${ROSTERCHIEF_ROLE:-full}
domain=$ROSTERCHIEF_BASE_DOMAIN
control=control.$domain

fail() { printf '%s\n' "$1" >&2; exit 1; }

units=()
if rc_has_backend "$role"; then units+=(postgresql valkey); fi
while IFS= read -r unit; do units+=("${unit%.service}"); done < <(rc_app_units "$role")
for unit in "${units[@]}"; do
    systemctl is-active --quiet "$unit.service" || fail "Unhealthy systemd unit: $unit"
done

insecure=()
[[ $(rc_access_mode) != ip ]] || insecure=(--insecure)  # reached by IP: Caddy's own CA signs the certificate
probe() { curl --fail --silent --show-error --max-time 8 --output /dev/null ${insecure[@]+"${insecure[@]}"} "$@"; }
sveltekit_headers() { printf '%s\n' -H "Host: $1" -H "X-Forwarded-Host: $1" -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 127.0.0.1'; }

if rc_has_backend "$role"; then
    probe -H "Host: $domain" http://127.0.0.1:8000/healthz || fail "Django /healthz failed"
    if [[ $role == backend ]]; then
        probe -H "Host: $domain" "http://${ROSTERCHIEF_BACKEND_BIND}:8001/healthz" || fail "Backend Caddy listener failed"
    fi
fi
if rc_has_frontend "$role"; then
    mapfile -t h < <(sveltekit_headers "$domain")
    probe "${h[@]}" http://127.0.0.1:5174/login || fail "Management frontend failed"
    mapfile -t h < <(sveltekit_headers "$control")
    probe "${h[@]}" http://127.0.0.1:5173/login || fail "Control panel frontend failed"
    if [[ $role == frontend ]]; then
        probe -H "Host: $domain" "${BACKEND_URL}/healthz" || fail "Cannot reach the backend at $BACKEND_URL"
    fi
fi

if [[ ${1:-} == --tls ]]; then
    systemctl is-active --quiet rosterchief-caddy.service || fail "Unhealthy systemd unit: rosterchief-caddy"
    if rc_has_frontend "$role"; then
        probe --resolve "$domain:443:127.0.0.1" "https://$domain/healthz" || fail "Public /healthz failed"
        probe --resolve "$domain:443:127.0.0.1" "https://$domain/login" || fail "Public /login failed"
        probe --resolve "$control:443:127.0.0.1" "https://$control/login" || fail "Control panel /login failed"
        seen=$(curl --silent --max-time 8 ${insecure[@]+"${insecure[@]}"} --resolve "$domain:443:127.0.0.1" -H 'CF-Connecting-IP: 203.0.113.9' "https://$domain/_rc/client-ip" || true)
        [[ $seen == 127.0.0.1 ]] || fail "Caddy trusts a forged CF-Connecting-IP from a non-Cloudflare peer (saw '$seen'). Check /etc/rosterchief/cloudflare-ips.caddy."
    fi
fi
