#!/usr/bin/env bash
# Back up what carries state: the database, and the uploads if they are still on local disk.
#
#   deploy/backup.sh /var/backups/rosterchief
#
# Step-by-step setup (encryption key, append-only offsite copy to a server over SSH, cron,
# restore test): DEPLOYMENT.md, "Backups". The backup server's half is
# deploy/backup-server/archive-incoming.sh.
#
# Runs from cron (see DEPLOYMENT.md). Exits non-zero on any failure, so cron mails you —
# a backup script that fails quietly is worse than no backup script, because you will
# believe you have backups.
#
# BACKUP_AGE_RECIPIENT (an age public key, "age1...") encrypts every archive to *.age and
# removes the plaintext; the matching private key lives offline, not on this server (see
# DEPLOYMENT.md's "Backups"). It is required whenever BACKUP_REMOTE is set: dumps
# of personal data never leave the box unencrypted. Like POSTGRES_USER/POSTGRES_DB, it is read
# from the environment first and otherwise from the compose .env in the current directory, so
# cron and deploy/deploy.sh's pre-migration backup behave the same.
#
# BARE_METAL=1 (what deploy/deploy.sh sets, and what the cron line on a bare-metal server should set):
# PostgreSQL runs on this host and the uploads are in /opt/rosterchief/shared, so there is no
# `docker compose exec`; run as root. The settings come from /etc/rosterchief/rosterchief.env.
set -Eeuo pipefail

# Dumps hold personal data (members, families, private uploads): everything this script
# creates is readable by the owning user only.
umask 077

DEST="${1:-/var/backups/rosterchief}"
COMPOSE="${COMPOSE:-docker compose}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%F-%H%M)"

# VAR from the environment, else from an env file in the current directory (default ./.env, the
# compose env file), else empty. Only reads the one line: env files are never sourced as shell.
from_env_file() {
    local name="$1" file="${2:-.env}" value="${!1:-}"
    if [ -z "$value" ] && [ -f "$file" ]; then
        value="$(sed -n "s/^${name}=//p" "$file" | tail -1 | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//')"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "$value"
}

POSTGRES_USER="$(from_env_file POSTGRES_USER)"
POSTGRES_DB="$(from_env_file POSTGRES_DB)"
BACKUP_AGE_RECIPIENT="$(from_env_file BACKUP_AGE_RECIPIENT)"
# Django's own setting lives in .env.production, not .env.
AWS_STORAGE_BUCKET_NAME="$(from_env_file AWS_STORAGE_BUCKET_NAME .env.production)"
BARE_METAL="${BARE_METAL:-0}"
SHARED_DIR=/opt/rosterchief/shared
if [ "$BARE_METAL" = 1 ]; then
    [ "$(id -u)" -eq 0 ] || { echo "ERROR: BARE_METAL=1 needs root (pg_dump as the postgres user, reads the app env file)." >&2; exit 1; }
    AWS_STORAGE_BUCKET_NAME="$(from_env_file AWS_STORAGE_BUCKET_NAME /etc/rosterchief/rosterchief.env)"
    BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-$(from_env_file BACKUP_AGE_RECIPIENT /etc/rosterchief/backup.env)}"
fi

if [ -n "${BACKUP_REMOTE:-}" ] && [ -z "$BACKUP_AGE_RECIPIENT" ]; then
    echo "ERROR: BACKUP_REMOTE is set but BACKUP_AGE_RECIPIENT is not -- refusing to copy unencrypted dumps off the box. See DEPLOYMENT.md, \"Backups\"." >&2
    exit 1
fi
if [ -n "$BACKUP_AGE_RECIPIENT" ] && ! command -v age >/dev/null 2>&1; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is set but the age binary is not on PATH (cron: set PATH to include /usr/local/bin). See DEPLOYMENT.md, \"Backups\" -> \"Installing the tools\"." >&2
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

if [ "$BARE_METAL" = 1 ]; then
    # Peer auth as the postgres OS user; the dump records the app role as owner, like the Docker one did.
    sudo -u postgres "$(ls -d /usr/pgsql-*/bin | sort -V | tail -1)/pg_dump" --clean --if-exists "${POSTGRES_DB:-rosterchief}" | gzip > "$DB_TMP"
else
    $COMPOSE exec -T db pg_dump --clean --if-exists -U "${POSTGRES_USER:-rosterchief}" "${POSTGRES_DB:-rosterchief}" | gzip > "$DB_TMP"
fi
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
    if [ "$BARE_METAL" = 1 ]; then tar -cz -C "$SHARED_DIR" media > "$MEDIA_OUT.part"; else $COMPOSE exec -T web tar -cz -C /app media | cat > "$MEDIA_OUT.part"; fi
    mv "$MEDIA_OUT.part" "$MEDIA_OUT"
    seal "$MEDIA_OUT"
fi

# Private uploads (e.g. medical certificates, see rosterchief/storage.py) are always on local
# disk, whatever AWS_STORAGE_BUCKET_NAME says, so they are backed up unconditionally.
PRIVATE_OUT="$DEST/private-media-$STAMP.tar.gz"
if [ "$BARE_METAL" = 1 ]; then tar -cz -C "$SHARED_DIR" private_media > "$PRIVATE_OUT.part"; else $COMPOSE exec -T web tar -cz -C /app private_media | cat > "$PRIVATE_OUT.part"; fi
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
#
# The offsite copy is append-only from here: BACKUP_REMOTE points at the backup server's
# incoming/ folder, the upload key may create files there but not remove, rename or change
# them (sftp-server -P, see DEPLOYMENT.md), and a root job on the backup server
# (deploy/backup-server/archive-incoming.sh) moves each finished upload out of this server's
# reach. So whoever takes over this server can't delete or overwrite the offsite backups -- and
# this script can't prune them either: retention lives on the backup server (ARCHIVE_KEEP_DAYS).
#
# Because uploads disappear from incoming/, rclone can't tell what it sent before by looking at
# the remote. $DEST/.offsite-sent records every file uploaded successfully; each run uploads
# only the *.age files not on that list (so a failed night is retried the next night, and a
# pre-deploy backup goes out with the next nightly run). A file re-sent after a partial failure
# is harmless: the archive job drops identical re-uploads.
#
# Upload flags, all so that nothing but "create and write" is needed on the server:
#   --inplace                 write straight to the final name. By default rclone >= 1.63
#                             uploads to <name>.<hash>.partial and renames it, and rename is
#                             denied. Older rclone has no such flag and already writes in place.
#   --sftp-set-modtime=false  don't set the file's mtime afterwards (setstat, denied).
#   --no-check-dest           don't compare with the remote; just upload the listed files.
if [ -n "${REMOTE_KEEP_DAYS:-}" ]; then
    echo "NOTE: REMOTE_KEEP_DAYS is ignored now -- this server can no longer delete offsite backups (by design). Retention is ARCHIVE_KEEP_DAYS on the backup server; remove REMOTE_KEEP_DAYS from the cron line. See DEPLOYMENT.md, \"Backups\"." >&2
fi
if [ -n "${BACKUP_REMOTE:-}" ]; then
    case "${BACKUP_REMOTE%/}" in
        */incoming) ;;
        *) echo "NOTE: BACKUP_REMOTE=$BACKUP_REMOTE does not end in /incoming; the backup server only archives what lands in its incoming folder (e.g. offsite:rosterchief/incoming). See DEPLOYMENT.md, \"Backups\"." >&2 ;;
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
