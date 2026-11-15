#!/usr/bin/env bash
set -euo pipefail

if [[ -z ${ROSTERCHIEF_BASE_DOMAIN:-} ]]; then
    source /etc/rosterchief/site.env
fi
domain=$ROSTERCHIEF_BASE_DOMAIN
control=control.$domain

for unit in postgresql valkey rosterchief-web rosterchief-worker rosterchief-beat rosterchief-management rosterchief-controlpanel; do
    if ! systemctl is-active --quiet "$unit.service"; then
        printf 'Unhealthy systemd unit: %s\n' "$unit" >&2
        exit 1
    fi
done

probe() { curl --fail --silent --show-error --max-time 8 --output /dev/null "$@"; }
if [[ ${1:-} == --tls ]]; then
    systemctl is-active --quiet rosterchief-caddy.service
    probe --resolve "$domain:443:127.0.0.1" "https://$domain/healthz"
    probe --resolve "$domain:443:127.0.0.1" "https://$domain/login"
    probe --resolve "$control:443:127.0.0.1" "https://$control/login"
else
    probe -H "Host: $domain" http://127.0.0.1:8000/healthz
    probe -H "Host: $domain" -H "X-Forwarded-Host: $domain" -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 127.0.0.1' http://127.0.0.1:5174/login
    probe -H "Host: $control" -H "X-Forwarded-Host: $control" -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 127.0.0.1' http://127.0.0.1:5173/login
fi
