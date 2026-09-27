#!/usr/bin/env bash
# Restore the latest dump into a throwaway database and count the rows.
#
#   deploy/restore-check.sh [/var/backups/rosterchief]
#
# The step that proves the backups work: run it regularly (DEPLOYMENT.md, "Backups", step 6).
# A dump you have never restored is a hypothesis, not a backup.
#
# Encrypted dumps (db-*.sql.gz.age, see deploy/backup.sh) need the age private key:
# BACKUP_AGE_IDENTITY=/path/to/key.txt. That key is meant to live offline, so either run this
# where the key is (with a copy of the dump and a scratch Postgres), or plug the key in for the
# rehearsal and remove it afterwards -- see DEPLOYMENT.md's "Backups", step 6.
set -Eeuo pipefail

DEST="${1:-/var/backups/rosterchief}"
COMPOSE="${COMPOSE:-docker compose}"
USER_NAME="${POSTGRES_USER:-rosterchief}"
SCRATCH="restore_check_$(date +%s)"

LATEST="$(ls -1t "$DEST"/db-*.sql.gz "$DEST"/db-*.sql.gz.age 2>/dev/null | head -1)"
[ -n "$LATEST" ] || { echo "no dump found in $DEST"; exit 1; }

# Plaintext SQL on stdout, whichever form the dump is in.
read_dump() {
    case "$LATEST" in
        *.age) age -d -i "$BACKUP_AGE_IDENTITY" "$LATEST" | gunzip -c ;;
        *) gunzip -c "$LATEST" ;;
    esac
}
if [[ "$LATEST" == *.age ]] && [ -z "${BACKUP_AGE_IDENTITY:-}" ]; then
    echo "restore check: $(basename "$LATEST") is encrypted; set BACKUP_AGE_IDENTITY to the age private key file" >&2
    exit 1
fi

cleanup() { $COMPOSE exec -T db dropdb -U "$USER_NAME" --if-exists "$SCRATCH" >/dev/null 2>&1 || true; }
trap cleanup EXIT

$COMPOSE exec -T db createdb -U "$USER_NAME" "$SCRATCH"
read_dump | $COMPOSE exec -T db psql -q -U "$USER_NAME" "$SCRATCH" >/dev/null

# A restore that produces an empty schema exits 0 and tells you nothing. Ask it something.
CLUBS="$($COMPOSE exec -T db psql -tAq -U "$USER_NAME" "$SCRATCH" -c 'SELECT count(*) FROM club_club')"
USERS="$($COMPOSE exec -T db psql -tAq -U "$USER_NAME" "$SCRATCH" -c 'SELECT count(*) FROM authentication_user')"

[ "$USERS" -gt 0 ] || { echo "restore check FAILED: $(basename "$LATEST") restored no users"; exit 1; }

echo "$(date -Iseconds) restore ok: $(basename "$LATEST") -> $CLUBS clubs, $USERS users"
