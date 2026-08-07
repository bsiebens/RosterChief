#!/usr/bin/env bash
# Restore the latest dump into a throwaway database and count the rows.
#
#   deploy/restore-check.sh [/var/backups/rosterchief]
#
# The only line in the backup cron that proves the others work. A dump you have never
# restored is a hypothesis, not a backup.
set -Eeuo pipefail

DEST="${1:-/var/backups/rosterchief}"
COMPOSE="${COMPOSE:-docker compose}"
USER_NAME="${POSTGRES_USER:-rosterchief}"
SCRATCH="restore_check_$(date +%s)"

LATEST="$(ls -1t "$DEST"/db-*.sql.gz 2>/dev/null | head -1)"
[ -n "$LATEST" ] || { echo "no dump found in $DEST"; exit 1; }

cleanup() { $COMPOSE exec -T db dropdb -U "$USER_NAME" --if-exists "$SCRATCH" >/dev/null 2>&1 || true; }
trap cleanup EXIT

$COMPOSE exec -T db createdb -U "$USER_NAME" "$SCRATCH"
gunzip -c "$LATEST" | $COMPOSE exec -T db psql -q -U "$USER_NAME" "$SCRATCH" >/dev/null

# A restore that produces an empty schema exits 0 and tells you nothing. Ask it something.
CLUBS="$($COMPOSE exec -T db psql -tAq -U "$USER_NAME" "$SCRATCH" -c 'SELECT count(*) FROM club_club')"
USERS="$($COMPOSE exec -T db psql -tAq -U "$USER_NAME" "$SCRATCH" -c 'SELECT count(*) FROM authentication_user')"

[ "$USERS" -gt 0 ] || { echo "restore check FAILED: $(basename "$LATEST") restored no users"; exit 1; }

echo "$(date -Iseconds) restore ok: $(basename "$LATEST") -> $CLUBS clubs, $USERS users"
