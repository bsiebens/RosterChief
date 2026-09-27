#!/usr/bin/env bash
# Back up what carries state: the database, and the uploads if they are still on local disk.
#
#   deploy/backup.sh /var/backups/rosterchief
#
# Runs from cron (see DEPLOYMENT.md). Exits non-zero on any failure, so cron mails you —
# a backup script that fails quietly is worse than no backup script, because you will
# believe you have backups.
#
# BACKUP_AGE_RECIPIENT (an age public key, "age1...") encrypts every archive to *.age and
# removes the plaintext; the matching private key lives offline, not on this server (see
# DEPLOYMENT.md's "Encrypted backups"). It is required whenever BACKUP_REMOTE is set: dumps
# of personal data never leave the box unencrypted. Like POSTGRES_USER/POSTGRES_DB, it is read
# from the environment first and otherwise from the compose .env in the current directory, so
# cron and deploy/deploy-prod.sh's pre-migration backup behave the same.
set -Eeuo pipefail

# Dumps hold personal data (members, families, private uploads): everything this script
# creates is readable by the owning user only.
umask 077

DEST="${1:-/var/backups/rosterchief}"
COMPOSE="${COMPOSE:-docker compose}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%F-%H%M)"

# VAR from the environment, else from ./.env (the compose env file), else empty. Only reads the
# one line: .env is never sourced as shell.
from_env_file() {
    local name="$1" value="${!1:-}"
    if [ -z "$value" ] && [ -f .env ]; then
        value="$(sed -n "s/^${name}=//p" .env | tail -1)"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "$value"
}

POSTGRES_USER="$(from_env_file POSTGRES_USER)"
POSTGRES_DB="$(from_env_file POSTGRES_DB)"
BACKUP_AGE_RECIPIENT="$(from_env_file BACKUP_AGE_RECIPIENT)"

if [ -n "${BACKUP_REMOTE:-}" ] && [ -z "$BACKUP_AGE_RECIPIENT" ]; then
    echo "ERROR: BACKUP_REMOTE is set but BACKUP_AGE_RECIPIENT is not -- refusing to copy unencrypted dumps off the box. See DEPLOYMENT.md, \"Encrypted backups\"." >&2
    exit 1
fi
if [ -n "$BACKUP_AGE_RECIPIENT" ] && ! command -v age >/dev/null 2>&1; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is set but the age binary is not installed (apt install age). See DEPLOYMENT.md." >&2
    exit 1
fi

# Encrypt a finished archive to "$1.age" (written via .part, like everything else here) and
# remove the plaintext. A no-op without BACKUP_AGE_RECIPIENT. Sets SEALED to the final path
# (a global rather than $(seal ...): errexit does not reach inside a command substitution).
seal() {
    local plain="$1"
    SEALED="$plain"
    [ -n "$BACKUP_AGE_RECIPIENT" ] || return 0
    age -r "$BACKUP_AGE_RECIPIENT" -o "$plain.age.part" "$plain"
    [ -s "$plain.age.part" ]
    mv "$plain.age.part" "$plain.age"
    rm -f "$plain"
    SEALED="$plain.age"
}

mkdir -p "$DEST"
chmod 700 "$DEST"

# --- database ---------------------------------------------------------------
# Written to a temporary name and moved into place only on success: a truncated dump that
# looks like a backup is the trap this avoids.
DB_TMP="$DEST/.db-$STAMP.sql.gz.part"
DB_OUT="$DEST/db-$STAMP.sql.gz"

$COMPOSE exec -T db pg_dump --clean --if-exists -U "${POSTGRES_USER:-rosterchief}" "${POSTGRES_DB:-rosterchief}" | gzip > "$DB_TMP"
gzip -t "$DB_TMP"                     # the archive is readable
[ -s "$DB_TMP" ]                      # ...and not empty
mv "$DB_TMP" "$DB_OUT"
seal "$DB_OUT"
DB_OUT="$SEALED"

# --- uploads ----------------------------------------------------------------
# Only while media is local. Once AWS_STORAGE_BUCKET_NAME is set the bucket's own versioning
# is the backup, and this step is skipped.
if [ -z "${AWS_STORAGE_BUCKET_NAME:-}" ]; then
    MEDIA_OUT="$DEST/media-$STAMP.tar.gz"
    $COMPOSE exec -T web tar -cz -C /app media | cat > "$MEDIA_OUT.part"
    mv "$MEDIA_OUT.part" "$MEDIA_OUT"
    seal "$MEDIA_OUT"
fi

# Private uploads (e.g. medical certificates, see rosterchief/storage.py) are always on local
# disk, whatever AWS_STORAGE_BUCKET_NAME says, so they are backed up unconditionally.
PRIVATE_OUT="$DEST/private-media-$STAMP.tar.gz"
$COMPOSE exec -T web tar -cz -C /app private_media | cat > "$PRIVATE_OUT.part"
mv "$PRIVATE_OUT.part" "$PRIVATE_OUT"
seal "$PRIVATE_OUT"

# --- retention --------------------------------------------------------------
for pattern in 'db-*.sql.gz' 'media-*.tar.gz' 'private-media-*.tar.gz'; do
    find "$DEST" \( -name "$pattern" -o -name "$pattern.age" \) -mtime "+$KEEP_DAYS" -delete
done
find "$DEST" -name '*.part' -mtime +1 -delete

echo "$(date -Iseconds) backup ok: $(basename "$DB_OUT") ($(du -h "$DB_OUT" | cut -f1))"

# --- offsite ----------------------------------------------------------------
# A backup on the same disk as the database is not a backup: it survives a bad migration, but
# not the server. Set BACKUP_REMOTE to an rclone remote to copy it off the box. Only the
# encrypted *.age files are copied (BACKUP_AGE_RECIPIENT is required above for this); any
# plaintext archives left from before encryption was turned on stay local.
if [ -n "${BACKUP_REMOTE:-}" ]; then
    rclone copy "$DEST" "$BACKUP_REMOTE" --max-age "${KEEP_DAYS}d" --include '*.age'
    echo "$(date -Iseconds) copied to $BACKUP_REMOTE"
fi
