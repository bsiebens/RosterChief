#!/usr/bin/env bash
# Convert a running Docker (compose) RosterChief install to a prepared bare-metal server, WITHOUT losing
# data: dump the database and uploads, restore them on the new server, and prove the copy is complete by
# comparing the row count of every table on both sides. Run it from your own machine.
#
#   deploy/convert.sh --source deploy@old.example.com --source-dir /home/deploy/RosterChief \
#                     --target root@new.example.com
#
#   --source USER@HOST     the Docker server (read-only to this script, except for maintenance mode)
#   --source-dir DIR       the compose checkout on it (where compose.yaml and .env live)
#   --compose-file FILE    default compose.yaml (compose.behind-proxy.yaml on the test server)
#   --target USER@HOST     the new master, already prepared with deploy/prep-server.sh --master
#   --from-dump FILE       restore an existing backup (db-*.sql.gz) instead of dumping the source; the
#                          row-count comparison is then skipped (there is no live source to compare to)
#   --no-freeze            don't put the source in maintenance mode first (a rehearsal; writes made after
#                          the dump are NOT carried over, and the final comparison will flag them)
#   --no-copy-secrets      don't carry DJANGO_SECRET_KEY / MFA_ENCRYPTION_KEY over from the source
#   --key FILE             ssh identity for both servers
#   --out DIR              where the local copy of the dump is kept (default ./convert-<timestamp>)
#
# What it does, in order -- nothing touches the new server until the dump is complete and checksummed:
#   1. source: maintenance mode ON (no writes during the move);
#   2. source: pg_dump + the media and private_media volumes -> a local directory (kept afterwards: it is a
#      backup in its own right, and holds personal data, so it is created 0700);
#   3. target: refuse unless the application database is EMPTY (this never overwrites data);
#   4. target: stop web/worker/beat, restore the dump in ONE transaction (all or nothing), restore uploads;
#   5. compare per-table row counts, source vs target -- any difference fails the run;
#   6. carry DJANGO_SECRET_KEY and MFA_ENCRYPTION_KEY over: without the MFA key every authenticator
#      secret in the restored database is unreadable and users are locked out of two-factor.
# It stops there: the app is NOT started. Next: `deploy/deploy.sh` (migrates, installs the Celery beat
# schedule, starts everything), then point DNS at the new server. The source stays up in maintenance mode
# (reopen it with the command printed at the end) so you can roll back by switching DNS back.
set -Eeuo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
step() { printf '\033[1;34m  ->\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

SOURCE="" SOURCE_DIR="" TARGET="" COMPOSE_FILE="compose.yaml" FROM_DUMP="" FREEZE=1 COPY_SECRETS=1 KEY="" OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE="${2:?}"; shift 2 ;;
        --source-dir) SOURCE_DIR="${2:?}"; shift 2 ;;
        --compose-file) COMPOSE_FILE="${2:?}"; shift 2 ;;
        --target) TARGET="${2:?}"; shift 2 ;;
        --from-dump) FROM_DUMP="${2:?}"; shift 2 ;;
        --no-freeze) FREEZE=0; shift ;;
        --no-copy-secrets) COPY_SECRETS=0; shift ;;
        --key) KEY="${2:?}"; shift 2 ;;
        --out) OUT="${2:?}"; shift 2 ;;
        -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
        *) die "Unknown argument '$1' (see --help)." ;;
    esac
done
[ -n "$TARGET" ] || die "--target is required."
if [ -z "$FROM_DUMP" ]; then
    [ -n "$SOURCE" ] && [ -n "$SOURCE_DIR" ] || die "Give --source and --source-dir, or --from-dump FILE."
else
    [ -f "$FROM_DUMP" ] || die "$FROM_DUMP not found."
    case "$FROM_DUMP" in *.age) die "Decrypt the .age dump first (age -d -i key.txt FILE > dump.sql.gz); this script restores plain .sql.gz." ;; esac
fi

SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)
[ -z "$KEY" ] || SSH_OPTS+=(-i "$KEY" -o IdentitiesOnly=yes)
src() { ssh "${SSH_OPTS[@]}" "$SOURCE" "$@"; }
tgt() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }
# Run a script on the target as root (sudo when the login isn't root), args as $1...
tgt_root() { local sudo=""; [[ "$TARGET" == root@* ]] || sudo="sudo -n "; ssh "${SSH_OPTS[@]}" "$TARGET" "${sudo}bash -s -- $(printf '%q ' "$@")"; }

OUT="${OUT:-./convert-$(date +%Y%m%d-%H%M%S)}"
umask 077
mkdir -p "$OUT"; chmod 700 "$OUT"
STAMP="$(date +%F-%H%M)"
DUMP="$OUT/db-$STAMP.sql.gz"

# --- preflight on the target (before the source is touched) -------------------
say "Checking the target"
tgt_root <<'REMOTE'
set -Eeuo pipefail
[ -d /opt/rosterchief/shared ] && [ -x /usr/local/bin/rosterchief-manage ] || { echo "Target is not prepared: run deploy/prep-server.sh --master first." >&2; exit 1; }
systemctl is-active --quiet "postgresql-17" || { echo "PostgreSQL is not running on the target." >&2; exit 1; }
tables="$(sudo -u postgres /usr/pgsql-17/bin/psql -tA -d rosterchief -c "select count(*) from information_schema.tables where table_schema='public'")"
[ "$tables" = 0 ] || { echo "The target database 'rosterchief' already has $tables tables. This script only restores into an EMPTY database, so it can never overwrite data. Drop and recreate it if you are sure." >&2; exit 1; }
REMOTE

# SQL that prints "table|rowcount" for every public table, sorted. One statement, so the numbers are consistent.
COUNT_SQL="SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"

# --- 1+2. the source ----------------------------------------------------------
if [ -z "$FROM_DUMP" ]; then
    DC="cd $(printf '%q' "$SOURCE_DIR") && docker compose -f $(printf '%q' "$COMPOSE_FILE")"
    PGUSER="$(src "cd $(printf '%q' "$SOURCE_DIR") && sed -n 's/^POSTGRES_USER=//p' .env 2>/dev/null | tail -1" || true)"; PGUSER="${PGUSER:-rosterchief}"
    PGDB="$(src "cd $(printf '%q' "$SOURCE_DIR") && sed -n 's/^POSTGRES_DB=//p' .env 2>/dev/null | tail -1" || true)"; PGDB="${PGDB:-rosterchief}"

    if [ "$FREEZE" = 1 ]; then
        say "Source: entering maintenance mode"
        src "$DC exec -T web python manage.py shell -c \"from features.models import Maintenance; Maintenance.start(message='RosterChief is moving to a new server. It will be back shortly.')\"" </dev/null
    else
        say "Not freezing the source (--no-freeze): this is a rehearsal"
    fi

    say "Source: dumping the database"
    src "$DC exec -T db pg_dump --clean --if-exists -U $PGUSER $PGDB" | gzip > "$DUMP.part"
    gzip -t "$DUMP.part"; [ -s "$DUMP.part" ]; mv "$DUMP.part" "$DUMP"

    say "Source: copying the uploads"
    src "$DC exec -T web tar -cz -C /app media" > "$OUT/media-$STAMP.tar.gz"
    src "$DC exec -T web tar -cz -C /app private_media" > "$OUT/private-media-$STAMP.tar.gz"
    gzip -t "$OUT/media-$STAMP.tar.gz" "$OUT/private-media-$STAMP.tar.gz"

    src "$DC exec -T db psql -tA -U $PGUSER -d $PGDB -c \"$COUNT_SQL\"" > "$OUT/counts-source.txt"
    [ -s "$OUT/counts-source.txt" ] || die "Could not read the source row counts."
    step "source has $(wc -l < "$OUT/counts-source.txt" | tr -d ' ') tables, $(awk -F'|' '{s+=$2} END {print s}' "$OUT/counts-source.txt") rows in total"
else
    cp "$FROM_DUMP" "$DUMP"
fi
( cd "$OUT" && shasum -a 256 -- *.gz > SHA256SUMS )
step "local copy kept in $OUT"

# --- 4. the target -------------------------------------------------------------
say "Target: uploading"
tgt "mkdir -p -m 700 /tmp/rosterchief-convert"
scp "${SSH_OPTS[@]}" -q "$OUT"/*.gz "$OUT/SHA256SUMS" "${TARGET}:/tmp/rosterchief-convert/"

say "Target: restoring (stopping the app, then one all-or-nothing transaction)"
tgt_root <<'REMOTE'
set -Eeuo pipefail
cd /tmp/rosterchief-convert
sha256sum -c SHA256SUMS >/dev/null || { echo "Checksum mismatch after upload." >&2; exit 1; }
systemctl stop rosterchief-web rosterchief-worker rosterchief-beat 2>/dev/null || true
dump="$(ls -1 db-*.sql.gz | head -1)"
# --single-transaction + ON_ERROR_STOP: either the whole dump lands or nothing does.
gunzip -c "$dump" | sudo -u postgres /usr/pgsql-17/bin/psql -X -q -v ON_ERROR_STOP=1 --single-transaction -d rosterchief >/dev/null
for kind in media private-media; do
    f="$(ls -1 ${kind}-*.tar.gz 2>/dev/null | head -1 || true)"; [ -n "$f" ] || continue
    dir="${kind//-/_}"
    tar -xzf "$f" -C /opt/rosterchief/shared
    chown -R rosterchief:rosterchief "/opt/rosterchief/shared/$dir"
done
restorecon -R /opt/rosterchief/shared/media 2>/dev/null || true
echo "restored $(sudo -u postgres /usr/pgsql-17/bin/psql -tA -d rosterchief -c "select count(*) from information_schema.tables where table_schema='public'") tables"
REMOTE

# --- 5. prove it ----------------------------------------------------------------
if [ -z "$FROM_DUMP" ]; then
    say "Comparing row counts, table by table"
    tgt_root "$COUNT_SQL" > "$OUT/counts-target.txt" <<'REMOTE'
set -Eeuo pipefail
sudo -u postgres /usr/pgsql-17/bin/psql -tA -d rosterchief -c "$1"
REMOTE
    if diff -u "$OUT/counts-source.txt" "$OUT/counts-target.txt"; then
        step "identical: every table has the same number of rows on both servers"
    else
        die "Row counts differ (above). Nothing was started on the target. The source is untouched, still in maintenance mode, and the dump is in $OUT."
    fi
else
    say "Skipping the row-count comparison (--from-dump: no live source)"
fi

# --- 6. secrets -------------------------------------------------------------------
if [ "$COPY_SECRETS" = 1 ] && [ -z "$FROM_DUMP" ]; then
    say "Carrying DJANGO_SECRET_KEY and MFA_ENCRYPTION_KEY over"
    SECRETS="$(src "cd $(printf '%q' "$SOURCE_DIR") && grep -E '^(DJANGO_SECRET_KEY|MFA_ENCRYPTION_KEY)=.+' .env.production" || true)"
    if [ -z "$SECRETS" ]; then
        printf '\033[1;33mWARNING:\033[0m could not read the keys from the source .env.production -- copy them by hand BEFORE deploying, or MFA users are locked out.\n' >&2
    else
        # The keys travel as an argument: stdin is the script itself (bash -s).
        tgt_root "$SECRETS" <<'REMOTE'
set -Eeuo pipefail
f=/etc/rosterchief/rosterchief.env
while IFS= read -r line; do
    key="${line%%=*}"
    grep -v "^${key}=" "$f" > "$f.new" || true
    printf '%s\n' "$line" >> "$f.new"
    cat "$f.new" > "$f"; rm -f "$f.new"
done <<<"$1"
REMOTE
    fi
fi
tgt "rm -rf /tmp/rosterchief-convert"

say "Done. The data is on the target and verified; the app there is NOT started yet."
echo "  1. Check /etc/rosterchief/rosterchief.env on the target (email settings, RESEND_API_KEY, S3 bucket if used)."
echo "  2. deploy/deploy.sh --version vX.Y.Z --master <target-host>   (migrates, installs the beat schedule, starts everything)"
echo "  3. Switch DNS (bare domain and *.domain) to the new server."
if [ -z "$FROM_DUMP" ] && [ "$FREEZE" = 1 ]; then
    echo "  The old server is still in maintenance mode. To roll back, point DNS back and run there:"
    echo "    cd ${SOURCE_DIR} && docker compose -f ${COMPOSE_FILE} exec -T web python manage.py shell -c \"from features.models import Maintenance; Maintenance.stop()\""
fi
