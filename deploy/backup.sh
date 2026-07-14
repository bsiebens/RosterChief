#!/usr/bin/env bash
# Back up what carries state: the database, and the uploads if they are still on local disk.
#
#   deploy/backup.sh /var/backups/rosterchief
#
# Runs from cron (see DEPLOYMENT.md). Exits non-zero on any failure, so cron mails you —
# a backup script that fails quietly is worse than no backup script, because you will
# believe you have backups.
set -Eeuo pipefail

DEST="${1:-/var/backups/rosterchief}"
COMPOSE="${COMPOSE:-docker compose}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%F-%H%M)"

mkdir -p "$DEST"

# --- database ---------------------------------------------------------------
# Written to a temporary name and moved into place only on success: a truncated dump that
# looks like a backup is the trap this avoids.
DB_TMP="$DEST/.db-$STAMP.sql.gz.part"
DB_OUT="$DEST/db-$STAMP.sql.gz"

$COMPOSE exec -T db pg_dump --clean --if-exists -U "${POSTGRES_USER:-rosterchief}" "${POSTGRES_DB:-rosterchief}" | gzip > "$DB_TMP"
gzip -t "$DB_TMP"                     # the archive is readable
[ -s "$DB_TMP" ]                      # ...and not empty
mv "$DB_TMP" "$DB_OUT"

# --- uploads ----------------------------------------------------------------
# Only while media is local. Once AWS_STORAGE_BUCKET_NAME is set the bucket's own versioning
# is the backup, and this step is skipped.
if [ -z "${AWS_STORAGE_BUCKET_NAME:-}" ]; then
    MEDIA_OUT="$DEST/media-$STAMP.tar.gz"
    $COMPOSE exec -T web tar -cz -C /app media | cat > "$MEDIA_OUT.part"
    mv "$MEDIA_OUT.part" "$MEDIA_OUT"
fi

# --- retention --------------------------------------------------------------
find "$DEST" -name 'db-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -name 'media-*.tar.gz' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -name '*.part' -mtime +1 -delete

echo "$(date -Iseconds) backup ok: $(basename "$DB_OUT") ($(du -h "$DB_OUT" | cut -f1))"

# --- offsite ----------------------------------------------------------------
# A backup on the same disk as the database is not a backup: it survives a bad migration, but
# not the server. Set BACKUP_REMOTE to an rclone remote to copy it off the box.
if [ -n "${BACKUP_REMOTE:-}" ]; then
    rclone copy "$DEST" "$BACKUP_REMOTE" --max-age "${KEEP_DAYS}d"
    echo "$(date -Iseconds) copied to $BACKUP_REMOTE"
fi
