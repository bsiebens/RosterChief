#!/usr/bin/env bash
# Sign in through a club's web app (handles the 2FA step) and print the session cookie value, for testing the API with curl.
#
#   deploy/api-token.sh sharks.rosterchief.app user@example.com        # prompts for password + 2FA code
#   export TOKEN=$(deploy/api-token.sh sharks.rosterchief.app user@example.com)
#   curl -b "rc_mgmt_session=$TOKEN" https://sharks.rosterchief.app/api/v1/me
#
# The web app proxies /_allauth/ and /api/v1/ and keeps the allauth session token in the httpOnly rc_mgmt_session
# cookie: it strips meta.session_token from responses and ignores an X-Session-Token header from the client. So this
# script signs in with a cookie jar and prints the cookie's value (which is the allauth session token).
#
# Optional env: RC_PASSWORD, RC_CODE (skip the prompts). Prompts and errors go to stderr; only the token goes to stdout.
set -euo pipefail

HOST="${1:?usage: $0 <host> <email>}"
EMAIL="${2:?usage: $0 <host> <email>}"
BASE="https://${HOST}/_allauth/app/v1/auth"
COOKIE="rc_mgmt_session"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

PASSWORD="${RC_PASSWORD:-}"
if [[ -z "$PASSWORD" ]]; then read -rsp "Password: " PASSWORD >&2; echo >&2; fi

post() { curl -sS -b "$JAR" -c "$JAR" -X POST "$1" -H "Content-Type: application/json" --data "$2"; }
token() { awk -v n="$COOKIE" '$6 == n { print $7 }' "$JAR" | tail -n 1; }

resp=$(post "$BASE/login" "$(jq -n --arg e "$EMAIL" --arg p "$PASSWORD" '{email:$e,password:$p}')")
status=$(jq -r '.status' <<<"$resp")

if [[ "$status" == "401" ]]; then
  # Password accepted; a pending 2FA stage is expected. Anything else is a failure.
  if ! jq -e '.data.flows[]? | select(.id == "mfa_authenticate" and .is_pending)' <<<"$resp" >/dev/null; then
    echo "Login failed: $resp" >&2; exit 1
  fi
  if [[ -z "$(token)" ]]; then echo "Login did not set the $COOKIE cookie: $resp" >&2; exit 1; fi
  CODE="${RC_CODE:-}"
  if [[ -z "$CODE" ]]; then read -rp "2FA code: " CODE >&2; fi
  resp=$(post "$BASE/2fa/authenticate" "$(jq -n --arg c "$CODE" '{code:$c}')")
  status=$(jq -r '.status' <<<"$resp")
fi

if [[ "$status" != "200" ]]; then echo "Failed ($status): $resp" >&2; exit 1; fi
t=$(token)
if [[ -z "$t" ]]; then echo "Signed in but no $COOKIE cookie was set: $resp" >&2; exit 1; fi
echo "$t"
