#!/usr/bin/env bash
# Runs on the BACKUP server, as root, from cron (every 15 minutes). Moves finished uploads out of
# the backup account's reach, and prunes old ones.
#
#   sudo install -m 0700 -o root -g root archive-incoming.sh /usr/local/sbin/rosterchief-archive-incoming
#   # root's crontab:
#   */15 * * * *  ARCHIVE_LOG=/var/log/rosterchief-archive.log /usr/local/sbin/rosterchief-archive-incoming
#
# Full setup: DEPLOYMENT.md, "Backups".
#
# Why: the production server uploads with an SSH key whose sftp-server may not remove, rename or
# change files (sftp-server -P ...). It can still *create* files, and open an existing one for
# writing -- so anything left in the upload folder could be truncated by whoever steals that key.
# This script copies every finished *.age upload into ARCHIVE_DIR (root-only, mode 700: the backup
# account can neither read nor write it), checks the copy, and only then deletes the upload. A
# stolen production server can then damage at most the last few minutes of uploads, never the
# archive.
#
# Copy-then-delete rather than mv, on purpose: a moved file keeps its inode, and an sftp session
# that still has it open could keep writing into the archived copy. The archive only ever holds
# root-owned files this script wrote itself.
#
# A file is taken only when
#   - it is a regular file (not a symlink) directly in INCOMING_DIR, named <safe chars>.age
#     (rclone's in-flight names end in .partial, the backup script's own in .part: never taken);
#   - it hasn't been written to for MIN_AGE_MINUTES (an upload still in progress keeps its mtime
#     fresh; the backup account can't set mtimes, setstat is denied);
#   - it starts with the age header "age-encryption.org/v1". Anything else named *.age is moved to
#     REJECTED_DIR (root-only) with a warning -- never deleted, never archived.
# If ARCHIVE_DIR already has a file of that name: an identical upload is just removed (the
# production side re-sent it); a different one is kept next to it as <name>.dup-<stamp>.age with
# a warning, and the original is never overwritten.
#
# Settings (environment; the defaults are the documented layout):
#   BACKUP_USER        the upload account (default rosterchief-backup)
#   INCOMING_DIR       default ~BACKUP_USER/rosterchief/incoming
#   ARCHIVE_DIR        default /var/lib/rosterchief-backups/archive
#   REJECTED_DIR       default <ARCHIVE_DIR>/../rejected
#   ARCHIVE_KEEP_DAYS  prune archived (and rejected) files older than this; 0 keeps everything
#                      (default 60)
#   MIN_AGE_MINUTES    default 10
#   ARCHIVE_LOG        append routine messages to this file instead of stdout. Warnings and errors
#                      always go to stderr too, so with ARCHIVE_LOG set cron mails only problems.
#
# Exits non-zero when something needs a human (a rejected or conflicting upload, a failed copy),
# after handling every other file. Safe to run as often as you like.
set -Eeuo pipefail
umask 077

BACKUP_USER="${BACKUP_USER:-rosterchief-backup}"
ARCHIVE_DIR="${ARCHIVE_DIR:-/var/lib/rosterchief-backups/archive}"
ARCHIVE_DIR="${ARCHIVE_DIR%/}"
REJECTED_DIR="${REJECTED_DIR:-$(dirname "$ARCHIVE_DIR")/rejected}"
ARCHIVE_KEEP_DAYS="${ARCHIVE_KEEP_DAYS:-60}"
MIN_AGE_MINUTES="${MIN_AGE_MINUTES:-10}"
ARCHIVE_LOG="${ARCHIVE_LOG:-}"
AGE_HEADER="age-encryption.org/v1"
STATUS=0

log() {
    local line
    line="$(date '+%Y-%m-%dT%H:%M:%S%z') archive-incoming: $*"
    if [ -n "$ARCHIVE_LOG" ]; then printf '%s\n' "$line" >> "$ARCHIVE_LOG"; else printf '%s\n' "$line"; fi
}
warn() {
    local line
    line="$(date '+%Y-%m-%dT%H:%M:%S%z') archive-incoming: WARNING: $*"
    [ -z "$ARCHIVE_LOG" ] || printf '%s\n' "$line" >> "$ARCHIVE_LOG"
    printf '%s\n' "$line" >&2
    STATUS=1
}
die() {
    warn "$*"
    exit 1
}
trap 'die "failed at line $LINENO: $BASH_COMMAND"' ERR

case "$ARCHIVE_KEEP_DAYS" in '' | *[!0-9]*) die "ARCHIVE_KEEP_DAYS must be a whole number of days, got '$ARCHIVE_KEEP_DAYS'" ;; esac
case "$MIN_AGE_MINUTES" in '' | *[!0-9]*) die "MIN_AGE_MINUTES must be a whole number of minutes, got '$MIN_AGE_MINUTES'" ;; esac

# Root, so the archive is out of the backup account's reach. ARCHIVE_ALLOW_NON_ROOT=1 is for
# trying the script out in a scratch directory, nothing else.
if [ "$(id -u)" != "0" ] && [ "${ARCHIVE_ALLOW_NON_ROOT:-}" != "1" ]; then
    die "run as root (the archive must be unreachable for $BACKUP_USER)"
fi

if [ -z "${INCOMING_DIR:-}" ]; then
    home="$(getent passwd "$BACKUP_USER" | cut -d: -f6 || true)"
    [ -n "$home" ] || die "no account '$BACKUP_USER' (set BACKUP_USER or INCOMING_DIR)"
    INCOMING_DIR="$home/rosterchief/incoming"
fi
INCOMING_DIR="${INCOMING_DIR%/}"

[ ! -L "$INCOMING_DIR" ] || die "$INCOMING_DIR is a symlink -- refusing"
[ -d "$INCOMING_DIR" ] || die "$INCOMING_DIR does not exist (DEPLOYMENT.md, \"Backups\", step 2)"

mkdir -p "$ARCHIVE_DIR" "$REJECTED_DIR"
chmod 700 "$(dirname "$ARCHIVE_DIR")" "$ARCHIVE_DIR" "$REJECTED_DIR"

# One run at a time (cron can overlap a slow run). flock is util-linux: present on every Linux.
if command -v flock >/dev/null 2>&1; then
    exec 9> "$(dirname "$ARCHIVE_DIR")/.archive-incoming.lock"
    flock -n 9 || { log "another run is in progress; skipping"; exit 0; }
fi

# Copy $1 to $2 as a new root-owned file, verify it, then delete $1.
take() {
    local src="$1" dst="$2" tmp
    tmp="$(dirname "$dst")/.$(basename "$dst").tmp"
    rm -f "$tmp"
    if ! cp -- "$src" "$tmp" || ! cmp -s -- "$src" "$tmp"; then
        rm -f "$tmp"
        warn "copying $src failed or the copy differs; left it in incoming"
        return 0
    fi
    chmod 600 "$tmp"
    mv -f -- "$tmp" "$dst"
    rm -f -- "$src"
}

archived=0
while IFS= read -r -d '' path; do
    name="$(basename "$path")"
    case "$name" in
        .* | *[!A-Za-z0-9._-]*)
            warn "unexpected file name '$name' in $INCOMING_DIR; left alone"
            continue
            ;;
    esac

    if [ ! -s "$path" ] || [ "$(head -c "${#AGE_HEADER}" -- "$path")" != "$AGE_HEADER" ]; then
        dst="$REJECTED_DIR/$name.$(date +%Y%m%d%H%M%S)"
        take "$path" "$dst"
        warn "$name is empty or not an age-encrypted file; moved to $dst"
        continue
    fi

    dst="$ARCHIVE_DIR/$name"
    if [ -e "$dst" ]; then
        if cmp -s -- "$path" "$dst"; then
            rm -f -- "$path"
            log "$name was already archived (identical re-upload); removed from incoming"
            continue
        fi
        dst="$ARCHIVE_DIR/${name%.age}.dup-$(date +%Y%m%d%H%M%S).age"
        take "$path" "$dst"
        warn "$name was uploaded again with different contents; archived original kept, new upload saved as $(basename "$dst")"
        continue
    fi

    take "$path" "$dst"
    if [ -e "$dst" ]; then
        log "archived $name ($(wc -c < "$dst" | tr -d ' ') bytes)"
        archived=$((archived + 1))
    fi
done < <(find "$INCOMING_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.age' -mmin "+$MIN_AGE_MINUTES" -print0)

# Leftovers of interrupted uploads (rclone before --inplace, or older than 1.63, writes
# <name>.<hash>.partial and can't remove it once remove is denied). A day old = abandoned.
find "$INCOMING_DIR" -mindepth 1 -maxdepth 1 -type f \( -name '*.partial' -o -name '*.part' \) -mmin +1440 -print -delete |
    while IFS= read -r stale; do log "removed abandoned partial upload $(basename "$stale")"; done

if [ "$ARCHIVE_KEEP_DAYS" != "0" ]; then
    find "$ARCHIVE_DIR" "$REJECTED_DIR" -mindepth 1 -maxdepth 1 -type f ! -name '.*' -mtime "+$ARCHIVE_KEEP_DAYS" -print -delete |
        while IFS= read -r old; do log "pruned $(basename "$old") (older than $ARCHIVE_KEEP_DAYS days)"; done
fi

[ "$archived" -eq 0 ] || log "done: $archived file(s) archived to $ARCHIVE_DIR"
exit "$STATUS"
