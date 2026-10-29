#!/usr/bin/env bash
# Restore the latest dump into a throwaway database and count the rows.
#
#   deploy/restore-check.sh [/var/backups/rosterchief]
#
# BARE_METAL=1: PostgreSQL runs on this host (run as root); the scratch database is created through the
# postgres OS user instead of `docker compose exec db`.
#
# The step that proves the backups work: run it regularly (DEPLOYMENT.md, "Backups", step 6).
# A dump you have never restored is a hypothesis, not a backup.
#
# Encrypted dumps (db-*.sql.gz.age, see deploy/backup.sh) need the age private key:
# BACKUP_AGE_IDENTITY=/path/to/key.txt. That key is meant to live offline, so either run this
# where the key is (with a copy of the dump and a scratch Postgres), or plug the key in for the
# rehearsal and remove it afterwards -- see DEPLOYMENT.md's "Backups", step 6. The offsite copies
# live in the backup server's root-only archive (/var/lib/rosterchief-backups/archive); fetch one
# with your admin login there into a directory and point this script at that directory.
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

BARE_METAL="${BARE_METAL:-0}"
if [ "$BARE_METAL" = 1 ]; then
    PGBIN="$(ls -d /usr/pgsql-*/bin | sort -V | tail -1)"
    pgx() { sudo -u postgres "$PGBIN/$1" "${@:2}"; }
    createdb_() { pgx createdb "$1"; }
    dropdb_() { pgx dropdb --if-exists "$1"; }
    psql_() { pgx psql "$@"; }
else
    createdb_() { $COMPOSE exec -T db createdb -U "$USER_NAME" "$1"; }
    dropdb_() { $COMPOSE exec -T db dropdb -U "$USER_NAME" --if-exists "$1"; }
    psql_() { $COMPOSE exec -T db psql -U "$USER_NAME" "$@"; }
fi

cleanup() { dropdb_ "$SCRATCH" >/dev/null 2>&1 || true; }
trap cleanup EXIT

createdb_ "$SCRATCH"
read_dump | psql_ -q "$SCRATCH" >/dev/null

# A restore that produces an empty schema exits 0 and tells you nothing. Ask it something.
CLUBS="$(psql_ -tAq "$SCRATCH" -c 'SELECT count(*) FROM club_club')"
USERS="$(psql_ -tAq "$SCRATCH" -c 'SELECT count(*) FROM authentication_user')"

[ "$USERS" -gt 0 ] || { echo "restore check FAILED: $(basename "$LATEST") restored no users"; exit 1; }

echo "$(date -Iseconds) restore ok: $(basename "$LATEST") -> $CLUBS clubs, $USERS users"
