#!/usr/bin/env bash
# One-time server preparation for a bare-metal RosterChief node (Rocky / AlmaLinux / RHEL 9+).
# Run it AS ROOT on the server itself; it is idempotent, so re-running after a change is safe.
#
#   # the first (master) node: Caddy + PostgreSQL + Redis + web + worker + beat
#   ./prep-server.sh --master --base-domain rosterchief.app --acme-email ops@rosterchief.app
#
#   # same, but with extra web nodes that will connect over a private network
#   ./prep-server.sh --master --base-domain rosterchief.app --acme-email ops@rosterchief.app \
#       --private-ip 10.0.0.1 --web-node-ip 10.0.0.2 --web-node-ip 10.0.0.3
#
#   # an additional web node: web + worker only (no Caddy, PostgreSQL, Redis or beat)
#   ./prep-server.sh --web-node --master-ip 10.0.0.1 --env-file ./rosterchief.env
#
# Roles:
#   --master     everything: Caddy (TLS, load balancer), PostgreSQL, Redis, gunicorn, Celery worker AND
#                Celery beat. There must be exactly one beat fleet-wide.
#   --web-node   gunicorn + Celery worker, talking to the master's PostgreSQL and Redis.
#   Backups are set up on any node that runs PostgreSQL: a nightly systemd timer (02:30) running
#   deploy/backup.sh, local copies in /var/backups/rosterchief (14 days). Give it --backup-age-recipient
#   <age public key> to encrypt them and --backup-remote <rclone remote> to copy them offsite (see
#   docs/DEPLOYMENT.md, "Backups", for creating the key and the append-only backup server);
#   --skip-backups opts out.
#   Fine-tune with --skip-caddy / --skip-postgres / --skip-redis / --skip-beat (e.g. a master that uses a
#   managed database: --skip-postgres, then point DJANGO_DATABASE_URL at it in the env file).
#
# What it sets up: uv + Python 3.14, the system libraries WeasyPrint/psycopg need, a `rosterchief` system
# user, /opt/rosterchief/{releases,shared}, the systemd units (deploy/systemd/), firewalld rules, SELinux
# labels, and /etc/rosterchief/rosterchief.env (secrets generated on the master; a web node takes a copy of
# the master's file via --env-file). It does NOT deploy the app: that is deploy/deploy.sh.
#
# Web nodes and uploads: club logos (media/) and private uploads (private_media/) live on the node's own
# disk, so with MORE THAN ONE web node an upload lands on only one of them. Set AWS_STORAGE_BUCKET_NAME for
# public media, and keep a single node serving until private_media is on shared storage.
set -Eeuo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root."

ROLE=""
BASE_DOMAIN=""
ACME_EMAIL=""
PRIVATE_IP=""          # master: the address web nodes reach PostgreSQL/Redis on
MASTER_IP=""           # web node: where the master is
WEB_NODE_IPS=()        # master: web nodes allowed through the firewall and used as Caddy upstreams
ENV_FILE=""            # web node: the master's rosterchief.env
SKIP_CADDY=0 SKIP_POSTGRES=0 SKIP_REDIS=0 SKIP_BEAT=0 SKIP_BACKUPS=0
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"   # age public key ("age1..."); encrypts every archive
BACKUP_REMOTE="${BACKUP_REMOTE:-}"                 # rclone remote for the offsite copy, e.g. offsite:rosterchief/incoming
PG_VERSION=17
DB_NAME="${POSTGRES_DB:-rosterchief}"
APP_DB_USER="${APP_DB_USER:-rosterchief_app}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # deploy/, for the systemd units and Caddyfile

while [ $# -gt 0 ]; do
    case "$1" in
        --master) ROLE=master; shift ;;
        --web-node) ROLE=web; shift ;;
        --base-domain) BASE_DOMAIN="${2:?}"; shift 2 ;;
        --acme-email) ACME_EMAIL="${2:?}"; shift 2 ;;
        --private-ip) PRIVATE_IP="${2:?}"; shift 2 ;;
        --master-ip) MASTER_IP="${2:?}"; shift 2 ;;
        --web-node-ip) WEB_NODE_IPS+=("${2:?}"); shift 2 ;;
        --env-file) ENV_FILE="${2:?}"; shift 2 ;;
        --skip-caddy) SKIP_CADDY=1; shift ;;
        --skip-postgres) SKIP_POSTGRES=1; shift ;;
        --skip-redis) SKIP_REDIS=1; shift ;;
        --skip-beat) SKIP_BEAT=1; shift ;;
        --skip-backups) SKIP_BACKUPS=1; shift ;;
        --backup-age-recipient) BACKUP_AGE_RECIPIENT="${2:?}"; shift 2 ;;
        --backup-remote) BACKUP_REMOTE="${2:?}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) die "Unknown argument '$1' (see --help)." ;;
    esac
done

[ -n "$ROLE" ] || die "Pick a role: --master or --web-node."
if [ "$ROLE" = web ]; then
    SKIP_CADDY=1 SKIP_POSTGRES=1 SKIP_REDIS=1 SKIP_BEAT=1
    [ -n "$MASTER_IP" ] || die "--web-node needs --master-ip."
    [ -f "$ENV_FILE" ] || die "--web-node needs --env-file: a copy of the master's /etc/rosterchief/rosterchief.env."
else
    [ -z "$MASTER_IP" ] || die "--master-ip is for --web-node."
    if [ "$SKIP_CADDY" = 0 ]; then
        [ -n "$BASE_DOMAIN" ] || die "--master needs --base-domain (or --skip-caddy)."
        [ -n "$ACME_EMAIL" ] || die "--master needs --acme-email (or --skip-caddy)."
        [ -n "${CLOUDFLARE_API_TOKEN:-}" ] || die "Export CLOUDFLARE_API_TOKEN (DNS:Edit on the zone): the wildcard certificate needs DNS-01."
    fi
    if [ "${#WEB_NODE_IPS[@]}" -gt 0 ] && [ -z "$PRIVATE_IP" ]; then
        die "--web-node-ip needs --private-ip: the address on this master that web nodes connect to."
    fi
fi

[ -r /etc/os-release ] && . /etc/os-release
case "${ID_LIKE:-} ${ID:-}" in
    *rhel*|*centos*|*rocky*|*almalinux*) ;;
    *) die "This script targets the RHEL family (Rocky/AlmaLinux); found '${PRETTY_NAME:-unknown}'." ;;
esac
EL_MAJOR="${VERSION_ID%%.*}"
[ "$EL_MAJOR" -ge 9 ] || die "Needs EL 9 or newer (found ${VERSION_ID})."

# --- base packages ----------------------------------------------------------
say "Base packages"
dnf -y install epel-release >/dev/null 2>&1 || true
# git: django-lucide is a git dependency, so `uv sync` shells out to it. pango/harfbuzz/...: WeasyPrint
# binds to them at import. gettext: compilemessages. libpq: psycopg. policycoreutils-python-utils: semanage.
dnf -y install curl tar gzip git gettext rsync firewalld \
    pango harfbuzz libffi libjpeg-turbo openjpeg2 shared-mime-info libpq \
    policycoreutils-python-utils >/dev/null
systemctl enable --now firewalld >/dev/null

# --- user and directories ---------------------------------------------------
say "User and directories"
id rosterchief >/dev/null 2>&1 || useradd --system --home-dir /var/lib/rosterchief --shell /sbin/nologin rosterchief
install -d -o root -g root -m 755 /opt/rosterchief /opt/rosterchief/releases
install -d -o rosterchief -g rosterchief -m 750 /opt/rosterchief/shared /opt/rosterchief/shared/media /opt/rosterchief/shared/private_media
install -d -o root -g rosterchief -m 750 /etc/rosterchief

# --- uv and Python ----------------------------------------------------------
if [ ! -x /usr/local/bin/uv ]; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
say "Installing Python 3.14 (shared by every release's virtualenv)"
UV_PYTHON_INSTALL_DIR=/opt/rosterchief/python /usr/local/bin/uv python install 3.14 >/dev/null
chmod -R a+rX /opt/rosterchief/python

# --- secrets / env file -----------------------------------------------------
rand() { openssl rand -hex "${1:-24}"; }
ENV_TARGET=/etc/rosterchief/rosterchief.env

if [ "$ROLE" = web ]; then
    say "Installing the master's env file (database/Redis hosts pointed at ${MASTER_IP})"
    sed -E "s#@(localhost|127\.0\.0\.1)([:/])#@${MASTER_IP}\2#" "$ENV_FILE" > "$ENV_TARGET"
elif [ ! -f "$ENV_TARGET" ]; then
    say "Generating $ENV_TARGET"
    DB_PASSWORD="$(rand)"
    REDIS_PASSWORD="$(rand)"
    FERNET="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
    cat > "$ENV_TARGET" <<EOF
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
MFA_ENCRYPTION_KEY=${FERNET}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=.${BASE_DOMAIN:-example.com}
DJANGO_CSRF_TRUSTED_ORIGINS=https://${BASE_DOMAIN:-example.com}
DJANGO_TIME_ZONE=Europe/Brussels
ROSTERCHIEF_BASE_DOMAIN=${BASE_DOMAIN:-example.com}
DJANGO_DATABASE_URL=postgres://${APP_DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}
DJANGO_REDIS_URL=redis://:${REDIS_PASSWORD}@localhost:6379/0
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True
DJANGO_STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage
# Still to fill in by hand -- see .env.production.example for the full list (email, RESEND_API_KEY, ...):
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EOF
    warn "Back up MFA_ENCRYPTION_KEY separately from the database (docs/DEPLOYMENT.md, 'Two-factor secrets'). Migrating an existing install? deploy/convert.sh copies the old key and secret key over."
else
    say "$ENV_TARGET exists -- leaving it alone"
fi
chown root:rosterchief "$ENV_TARGET"; chmod 640 "$ENV_TARGET"

# value of KEY from the env file (no shell sourcing: the file is data)
env_get() { sed -n "s/^$1=//p" "$ENV_TARGET" | tail -1; }
url_password() { sed -E 's#^[a-z+]+://[^:/@]*:([^@]*)@.*#\1#' <<<"$1"; }

# --- PostgreSQL --------------------------------------------------------------
if [ "$SKIP_POSTGRES" = 0 ]; then
    say "PostgreSQL ${PG_VERSION}"
    if ! rpm -q "postgresql${PG_VERSION}-server" >/dev/null 2>&1; then
        dnf -y install "https://download.postgresql.org/pub/repos/yum/reporpms/EL-${EL_MAJOR}-$(uname -m)/pgdg-redhat-repo-latest.noarch.rpm" >/dev/null 2>&1 || true
        dnf -qy module disable postgresql >/dev/null 2>&1 || true
        dnf -y install "postgresql${PG_VERSION}-server" >/dev/null
    fi
    PGDATA="/var/lib/pgsql/${PG_VERSION}/data"
    [ -f "$PGDATA/PG_VERSION" ] || "/usr/pgsql-${PG_VERSION}/bin/postgresql-${PG_VERSION}-setup" initdb >/dev/null
    systemctl enable "postgresql-${PG_VERSION}" >/dev/null

    LISTEN="localhost"
    [ -z "$PRIVATE_IP" ] || LISTEN="localhost,${PRIVATE_IP}"
    # Sized for a small box, not for the data (~0.2 GB after 5 years): web+worker per node, plus manage.py one-offs.
    cat > "$PGDATA/conf.d-rosterchief.conf" <<EOF
listen_addresses = '${LISTEN}'
shared_buffers = 128MB
max_connections = 60
password_encryption = scram-sha-256
EOF
    grep -q "conf.d-rosterchief.conf" "$PGDATA/postgresql.conf" || echo "include 'conf.d-rosterchief.conf'" >> "$PGDATA/postgresql.conf"

    # pg_hba: the app role, only from this host and the named web nodes. Managed block, rewritten each run.
    sed -i '/# BEGIN rosterchief/,/# END rosterchief/d' "$PGDATA/pg_hba.conf"
    {
        echo "# BEGIN rosterchief"
        echo "host ${DB_NAME} ${APP_DB_USER} 127.0.0.1/32 scram-sha-256"
        echo "host ${DB_NAME} ${APP_DB_USER} ::1/128 scram-sha-256"
        for ip in "${WEB_NODE_IPS[@]}"; do echo "host ${DB_NAME} ${APP_DB_USER} ${ip}/32 scram-sha-256"; done
        echo "# END rosterchief"
    } > "$PGDATA/pg_hba.conf.rosterchief"
    # The managed block goes in front of the stock rules, which first-match-wins.
    cat "$PGDATA/pg_hba.conf.rosterchief" "$PGDATA/pg_hba.conf" > "$PGDATA/pg_hba.conf.new"
    mv "$PGDATA/pg_hba.conf.new" "$PGDATA/pg_hba.conf"; rm -f "$PGDATA/pg_hba.conf.rosterchief"
    chown postgres:postgres "$PGDATA/pg_hba.conf" "$PGDATA/conf.d-rosterchief.conf"; chmod 600 "$PGDATA/pg_hba.conf"

    systemctl restart "postgresql-${PG_VERSION}"

    # The application role: owns the database and its public schema (so migrate can create tables), but is
    # not a superuser. Same shape as the old deploy/postgres/10-app-role.sh.
    DB_PASSWORD="$(url_password "$(env_get DJANGO_DATABASE_URL)")"
    [ -n "$DB_PASSWORD" ] || die "Could not read the database password from DJANGO_DATABASE_URL in $ENV_TARGET."
    PSQL=(sudo -u postgres "/usr/pgsql-${PG_VERSION}/bin/psql" -v ON_ERROR_STOP=1 -X -q)
    if [ "$("${PSQL[@]}" -tAc "SELECT 1 FROM pg_roles WHERE rolname='${APP_DB_USER}'")" != 1 ]; then
        "${PSQL[@]}" -v app_user="$APP_DB_USER" -v app_password="$DB_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SQL
    else
        "${PSQL[@]}" -v app_user="$APP_DB_USER" -v app_password="$DB_PASSWORD" <<'SQL'
ALTER ROLE :"app_user" PASSWORD :'app_password';
SQL
    fi
    if [ "$("${PSQL[@]}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" != 1 ]; then
        "${PSQL[@]}" -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${APP_DB_USER}\" ENCODING 'UTF8'"
    fi
    "${PSQL[@]}" -d "$DB_NAME" -v app_user="$APP_DB_USER" -v app_db="$DB_NAME" <<'SQL'
ALTER SCHEMA public OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
SQL
fi

# --- Redis -------------------------------------------------------------------
# Redis is BOTH the cache (waffle flags, Maintenance) and the Celery broker, so the old cache-only settings
# (32 MB, allkeys-lru, no persistence) are wrong here: they could evict queued tasks, and a restart would
# empty the queue.
#   maxmemory-policy volatile-lru   only keys WITH a TTL are evictable. Every cache entry has one (Django's
#                                   default timeout); Celery's queue and bookkeeping keys have none, so
#                                   they are never evicted. If memory is exhausted by non-evictable keys
#                                   Redis errors loudly instead of silently dropping jobs.
#   appendonly yes / everysec       a restart or reboot replays the queue (at most ~1 s lost).
#   maxmemory 256mb                 headroom for a task backlog (a stuck worker) on top of the cache.
if [ "$SKIP_REDIS" = 0 ]; then
    say "Redis"
    dnf -y module enable redis:7 >/dev/null 2>&1 || true
    dnf -y install redis >/dev/null
    REDIS_PASSWORD="$(url_password "$(env_get DJANGO_REDIS_URL)")"
    [ -n "$REDIS_PASSWORD" ] || die "Could not read the Redis password from DJANGO_REDIS_URL in $ENV_TARGET."
    BIND="127.0.0.1 -::1"; [ -z "$PRIVATE_IP" ] || BIND="127.0.0.1 -::1 ${PRIVATE_IP}"
    cat > /etc/redis/rosterchief.conf <<EOF
bind ${BIND}
protected-mode yes
requirepass ${REDIS_PASSWORD}
maxmemory 256mb
maxmemory-policy volatile-lru
appendonly yes
appendfsync everysec
EOF
    chown redis:redis /etc/redis/rosterchief.conf; chmod 640 /etc/redis/rosterchief.conf
    grep -q "^include /etc/redis/rosterchief.conf" /etc/redis/redis.conf || echo "include /etc/redis/rosterchief.conf" >> /etc/redis/redis.conf
    systemctl enable redis >/dev/null
    systemctl restart redis
fi

# --- Caddy -------------------------------------------------------------------
if [ "$SKIP_CADDY" = 0 ]; then
    say "Caddy (with the Cloudflare DNS plugin)"
    if ! command -v caddy >/dev/null 2>&1; then
        dnf -y install 'dnf-command(copr)' >/dev/null
        dnf -y copr enable @caddy/caddy >/dev/null
        dnf -y install caddy >/dev/null
    fi
    caddy list-modules 2>/dev/null | grep -q dns.providers.cloudflare || caddy add-package github.com/caddy-dns/cloudflare >/dev/null
    UPSTREAMS="127.0.0.1:8000"
    for ip in "${WEB_NODE_IPS[@]}"; do UPSTREAMS+=" ${ip}:8000"; done
    install -d -o root -g caddy -m 750 /etc/caddy
    cat > /etc/caddy/caddy.env <<EOF
ROSTERCHIEF_BASE_DOMAIN=${BASE_DOMAIN}
ACME_EMAIL=${ACME_EMAIL}
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}
ROSTERCHIEF_UPSTREAMS=${UPSTREAMS}
ROSTERCHIEF_MEDIA_ROOT=/opt/rosterchief/shared/media
EOF
    chown root:caddy /etc/caddy/caddy.env; chmod 640 /etc/caddy/caddy.env
    install -d /etc/systemd/system/caddy.service.d
    printf '[Service]\nEnvironmentFile=/etc/caddy/caddy.env\n' > /etc/systemd/system/caddy.service.d/rosterchief.conf
    if [ -f "$REPO_DIR/caddy/Caddyfile" ]; then
        install -o root -g caddy -m 640 "$REPO_DIR/caddy/Caddyfile" /etc/caddy/Caddyfile
    else
        warn "caddy/Caddyfile not found next to this script; deploy.sh installs it on the next deploy."
    fi
    # Caddy serves club logos straight off disk, and proxies to gunicorn on :8000.
    setsebool -P httpd_can_network_connect 1
    semanage fcontext -a -t httpd_sys_content_t '/opt/rosterchief/shared/media(/.*)?' 2>/dev/null \
        || semanage fcontext -m -t httpd_sys_content_t '/opt/rosterchief/shared/media(/.*)?'
    restorecon -R /opt/rosterchief/shared/media
    systemctl daemon-reload
    systemctl enable caddy >/dev/null
fi

# --- systemd units and node config -------------------------------------------
say "systemd units"
for unit in rosterchief-web rosterchief-worker rosterchief-beat; do
    install -m 644 "$REPO_DIR/systemd/${unit}.service" "/etc/systemd/system/${unit}.service"
done

if [ "$ROLE" = master ] && [ "${#WEB_NODE_IPS[@]}" -eq 0 ]; then
    WEB_BIND="127.0.0.1:8000"      # Caddy is on this box and is the only client
else
    WEB_BIND="0.0.0.0:8000"        # reachable by the master's Caddy; the firewall limits who
fi
cat > /etc/rosterchief/node.env <<EOF
ROSTERCHIEF_BIND=${WEB_BIND}
GUNICORN_WORKERS=2
GUNICORN_THREADS=4
CELERY_CONCURRENCY=2
EOF
chown root:rosterchief /etc/rosterchief/node.env; chmod 640 /etc/rosterchief/node.env
systemctl daemon-reload
systemctl enable rosterchief-web rosterchief-worker >/dev/null
if [ "$SKIP_BEAT" = 0 ]; then systemctl enable rosterchief-beat >/dev/null; else systemctl disable rosterchief-beat >/dev/null 2>&1 || true; fi

# A wrapper for `manage.py` against a release (default: current), as the app user with the app's environment.
cat > /usr/local/bin/rosterchief-manage <<'EOF'
#!/usr/bin/env bash
# rosterchief-manage [--release DIR] <manage.py args...>
set -Eeuo pipefail
DIR=/opt/rosterchief/current
if [ "${1:-}" = "--release" ]; then DIR="$2"; shift 2; fi
cd "$DIR"
exec sudo -u rosterchief env HOME=/var/lib/rosterchief XDG_CACHE_HOME=/var/lib/rosterchief/.cache \
    bash -c 'set -a; . /etc/rosterchief/rosterchief.env; set +a; exec "$@"' _ "$DIR/.venv/bin/python" manage.py "$@"
EOF
chmod 755 /usr/local/bin/rosterchief-manage
mkdir -p /var/lib/rosterchief/.cache; chown -R rosterchief:rosterchief /var/lib/rosterchief

# --- backups -----------------------------------------------------------------
if [ "$SKIP_POSTGRES" = 0 ] && [ "$SKIP_BACKUPS" = 0 ]; then
    say "Backups (nightly systemd timer)"
    if [ -n "$BACKUP_AGE_RECIPIENT" ]; then dnf -y install age >/dev/null 2>&1 || warn "Could not install 'age' from the repositories: install it (https://github.com/FiloSottile/age) before the first backup runs, or it fails."; fi
    if [ -n "$BACKUP_REMOTE" ]; then dnf -y install rclone >/dev/null 2>&1 || warn "Could not install 'rclone': install it before the first backup runs, or the offsite copy fails."; fi
    [ -z "$BACKUP_REMOTE" ] || [ -n "$BACKUP_AGE_RECIPIENT" ] || die "--backup-remote needs --backup-age-recipient: dumps of personal data never leave the box unencrypted."
    install -d -m 700 /var/backups/rosterchief
    umask 077
    {
        [ -z "$BACKUP_AGE_RECIPIENT" ] || echo "BACKUP_AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT}"
        [ -z "$BACKUP_REMOTE" ] || echo "BACKUP_REMOTE=${BACKUP_REMOTE}"
    } > /etc/rosterchief/backup.env
    chmod 600 /etc/rosterchief/backup.env; umask 022
    install -m 644 "$REPO_DIR/systemd/rosterchief-backup.service" /etc/systemd/system/rosterchief-backup.service
    install -m 644 "$REPO_DIR/systemd/rosterchief-backup.timer" /etc/systemd/system/rosterchief-backup.timer
    systemctl daemon-reload
    systemctl enable --now rosterchief-backup.timer >/dev/null
    [ -n "$BACKUP_AGE_RECIPIENT" ] || warn "No --backup-age-recipient: backups are stored UNENCRYPTED, locally only."
    [ -n "$BACKUP_REMOTE" ] || warn "No --backup-remote: backups stay on this server, which does not survive losing it."
fi

# --- firewall ----------------------------------------------------------------
say "Firewall"
fw() { firewall-cmd --permanent "$@" >/dev/null; }
fw --add-service=ssh
if [ "$SKIP_CADDY" = 0 ]; then fw --add-service=http; fw --add-service=https; fw --add-port=443/udp; fi
if [ "$ROLE" = web ]; then
    fw --add-rich-rule="rule family=ipv4 source address=${MASTER_IP} port port=8000 protocol=tcp accept"
else
    for ip in "${WEB_NODE_IPS[@]}"; do
        fw --add-rich-rule="rule family=ipv4 source address=${ip} port port=5432 protocol=tcp accept"
        fw --add-rich-rule="rule family=ipv4 source address=${ip} port port=6379 protocol=tcp accept"
    done
fi
firewall-cmd --reload >/dev/null

if [ "${#WEB_NODE_IPS[@]}" -gt 1 ] || [ "$ROLE" = web ]; then
    warn "More than one web node: uploads (media/, private_media/) are node-local. Set AWS_STORAGE_BUCKET_NAME for public media and see this script's header before sending traffic to several nodes."
fi

say "Server prepared (${ROLE}). Next:"
echo "  1. Review /etc/rosterchief/rosterchief.env (email, RESEND_API_KEY, ... -- see .env.production.example)."
[ "$ROLE" = web ] || echo "  2. Web nodes: scp this file to each, then run prep-server.sh --web-node --master-ip <ip> --env-file <file>."
echo "  3. From your machine: deploy/deploy.sh --version vX.Y.Z --master <host> [--web-node <host> ...]"
[ "$SKIP_POSTGRES" = 0 ] && [ "$SKIP_BACKUPS" = 0 ] && echo "  Backups start with the first deployed release; prove one restores with: BARE_METAL=1 /opt/rosterchief/current/deploy/restore-check.sh"
echo "  Migrating from the Docker setup? deploy/convert.sh first, then deploy."
