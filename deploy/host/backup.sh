#!/usr/bin/env bash
# Back up what carries state on a RosterChief host: the database and the uploads on local disk.
#
#   rosterchief-backup /var/backups/rosterchief
#
# Install as /usr/local/sbin/rosterchief-backup (mode 700). Run as root on the backend or full host
# only (frontend hosts hold no data). Setup, cron and restore test: docs/deployment.md, "Backups and
# the offsite copy". Exits non-zero on any failure, so cron mails you.
#
# BACKUP_AGE_RECIPIENT (an age public key, "age1...") encrypts every archive to *.age and removes the
# plaintext; the matching private key lives offline, not on this server. It is required whenever
# BACKUP_REMOTE is set: dumps of personal data never leave the box unencrypted. Both are read from the
# environment first, otherwise from /etc/rosterchief/backup.env.
set -Eeuo pipefail

# Dumps hold personal data: everything this script creates is readable by its owner only.
umask 077

DEST="${1:-/var/backups/rosterchief}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DATA_DIR="${DATA_DIR:-/var/lib/rosterchief}"
STAMP="$(date +%F-%H%M)"
FILE_PREFIX="${FILE_PREFIX:-}"      # optional, e.g. v2- to tell files apart in a shared archive

# VAR from the environment, else from an env file, else empty. Only reads the one line:
# env files are never sourced as shell.
from_env_file() {
    local name="$1" file="${2:-.env}" value="${!1:-}"
    if [ -z "$value" ] && [ -f "$file" ]; then
        value="$(sed -n "s/^${name}=//p" "$file" | tail -1 | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//')"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "$value"
}

BACKUP_AGE_RECIPIENT="$(from_env_file BACKUP_AGE_RECIPIENT /etc/rosterchief/backup.env)"
BACKUP_REMOTE="${BACKUP_REMOTE:-$(from_env_file BACKUP_REMOTE /etc/rosterchief/backup.env)}"
ROSTERCHIEF_DATABASE_NAME="$(from_env_file ROSTERCHIEF_DATABASE_NAME /etc/rosterchief/site.env)"
DB_NAME="${ROSTERCHIEF_DATABASE_NAME:-rosterchief}"

if [ -n "$BACKUP_REMOTE" ] && [ -z "$BACKUP_AGE_RECIPIENT" ]; then
    echo "ERROR: BACKUP_REMOTE is set but BACKUP_AGE_RECIPIENT is not -- refusing to copy unencrypted dumps off the box." >&2
    exit 1
fi
if [ -n "$BACKUP_AGE_RECIPIENT" ] && ! command -v age > /dev/null 2>&1; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is set but the age binary is not on PATH (cron: set PATH to include /usr/local/bin)." >&2
    exit 1
fi

# Encrypt a finished archive to "$1.age" (written via .part) and remove the plaintext. A no-op without
# BACKUP_AGE_RECIPIENT. Sets SEALED to the final path (a global rather than $(seal ...): errexit does
# not reach inside a command substitution).
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
# Written to a temporary name and moved into place only on success: a truncated dump that looks like a
# backup is the trap this avoids. Custom format (already compressed); covers every club's schema.
DB_TMP="$DEST/.${FILE_PREFIX}db-$STAMP.dump.part"
DB_OUT="$DEST/${FILE_PREFIX}db-$STAMP.dump"

runuser -u rosterchief -- pg_dump --format=custom --no-password --dbname "$DB_NAME" > "$DB_TMP"
pg_restore --list "$DB_TMP" > /dev/null     # the archive is readable
[ -s "$DB_TMP" ]                            # ...and not empty
mv "$DB_TMP" "$DB_OUT"
seal "$DB_OUT"
DB_OUT="$SEALED"

# --- uploads ----------------------------------------------------------------
# Public and private uploads (e.g. medical certificates) are both on local disk.
MEDIA_OUT="$DEST/${FILE_PREFIX}media-$STAMP.tar.gz"
tar -cz -C "$DATA_DIR" media > "$MEDIA_OUT.part"
mv "$MEDIA_OUT.part" "$MEDIA_OUT"
seal "$MEDIA_OUT"

PRIVATE_OUT="$DEST/${FILE_PREFIX}private-media-$STAMP.tar.gz"
tar -cz -C "$DATA_DIR" private_media > "$PRIVATE_OUT.part"
mv "$PRIVATE_OUT.part" "$PRIVATE_OUT"
seal "$PRIVATE_OUT"

# --- retention --------------------------------------------------------------
for pattern in "${FILE_PREFIX}db-*.dump" "${FILE_PREFIX}media-*.tar.gz" "${FILE_PREFIX}private-media-*.tar.gz"; do
    find "$DEST" \( -name "$pattern" -o -name "$pattern.age" \) -mtime "+$KEEP_DAYS" -delete
done
find "$DEST" -name '*.part' -mtime +1 -delete

echo "$(date -Iseconds) backup ok: $(basename "$DB_OUT") ($(du -h "$DB_OUT" | cut -f1))"

# --- offsite ----------------------------------------------------------------
# The offsite copy is append-only from here: BACKUP_REMOTE points at the backup server's incoming/
# folder, the upload key may create files there but not remove, rename or change them, and a root job
# on the backup server moves each finished upload out of this server's reach. This script can't prune
# offsite backups either: retention is ARCHIVE_KEEP_DAYS on the backup server.
#
# Because uploads disappear from incoming/, rclone can't tell what it sent before by looking at the
# remote. $DEST/.offsite-sent records every file uploaded successfully; each run uploads only the *.age
# files not on that list (so a failed night is retried the next night). A file re-sent after a partial
# failure is harmless: the archive job drops identical re-uploads.
#
# Upload flags, so that nothing but "create and write" is needed on the server:
#   --inplace                 write straight to the final name (rclone >= 1.63 would otherwise upload
#                             to <name>.partial and rename, which is denied).
#   --sftp-set-modtime=false  don't set mtime afterwards (setstat, denied).
#   --no-check-dest           don't compare with the remote; just upload the listed files.
if [ -n "$BACKUP_REMOTE" ]; then
    case "${BACKUP_REMOTE%/}" in
        */incoming) ;;
        *) echo "NOTE: BACKUP_REMOTE=$BACKUP_REMOTE does not end in /incoming; the backup server only archives what lands in its incoming folder (e.g. offsite:rosterchief/incoming)." >&2 ;;
    esac

    RCLONE_FLAGS=(--no-check-dest --sftp-set-modtime=false)
    read -r RCLONE_MAJOR RCLONE_MINOR < <(rclone version | sed -n '1s/^rclone v\([0-9]*\)\.\([0-9]*\).*/\1 \2/p') || true
    if [ "${RCLONE_MAJOR:-0}" -gt 1 ] || { [ "${RCLONE_MAJOR:-0}" -eq 1 ] && [ "${RCLONE_MINOR:-0}" -ge 63 ]; }; then
        RCLONE_FLAGS+=(--inplace)
    fi

    SENT="$DEST/.offsite-sent"
    PENDING="$DEST/.offsite-pending"
    LOCAL_AGE="$DEST/.offsite-local"
    touch "$SENT"
    find "$DEST" -mindepth 1 -maxdepth 1 -type f -name '*.age' ! -name '.*' -exec basename {} \; | sort > "$LOCAL_AGE"
    sort -u "$SENT" | comm -23 "$LOCAL_AGE" - > "$PENDING"

    if [ -s "$PENDING" ]; then
        rclone copy "$DEST" "$BACKUP_REMOTE" --files-from "$PENDING" "${RCLONE_FLAGS[@]}"
        cat "$PENDING" >> "$SENT"
        echo "$(date -Iseconds) copied $(wc -l < "$PENDING" | tr -d ' ') file(s) to $BACKUP_REMOTE"
    else
        echo "$(date -Iseconds) nothing new to copy to $BACKUP_REMOTE"
    fi
    # Forget files that local retention has removed, so the list stays short.
    sort -u "$SENT" | comm -12 - "$LOCAL_AGE" > "$SENT.new"
    mv "$SENT.new" "$SENT"
    rm -f "$PENDING" "$LOCAL_AGE"
fi
