#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID != 0 || $# -lt 2 || $# -gt 3 ]]; then
    echo "Usage: sudo $0 BASE_DOMAIN OWNER_EMAIL [PATH_TO_ROUTE53_CADDY]" >&2
    exit 2
fi
source /etc/os-release
if [[ ${ID:-} != amzn || ${VERSION_ID:-} != 2023 ]]; then
    echo "This bootstrap requires Amazon Linux 2023." >&2
    exit 2
fi

domain=$1
owner_email=$2
caddy_binary=${3:-}
if [[ ! $domain =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ || ! $owner_email =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z0-9]+$ ]]; then
    echo "Supply a lowercase public DNS domain and the platform owner's email address." >&2
    exit 2
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
app_dir=/opt/rosterchief
config_dir=/etc/rosterchief
data_dir=/var/lib/rosterchief

if [[ -f $config_dir/site.env ]]; then
    if ! grep -Fqx "ROSTERCHIEF_BASE_DOMAIN=$domain" "$config_dir/site.env" ||
       ! grep -Fqx "PLATFORM_OWNER_EMAIL=$owner_email" "$config_dir/site.env"; then
        echo "The host is already configured for another domain or owner." >&2
        exit 2
    fi
fi

dnf install -y git tar gzip openssl postgresql16-server valkey nodejs24 nodejs24-npm pango fontconfig dejavu-sans-fonts
for account in rosterchief rosterchief-build rosterchief-frontend caddy; do
    if ! id "$account" >/dev/null 2>&1; then
        useradd --system --user-group --shell /sbin/nologin --no-create-home "$account"
    fi
done

install -d -m 0755 "$app_dir" "$app_dir/releases" "$app_dir/bin" "$config_dir" "$data_dir"
install -d -m 0755 -o rosterchief-build -g rosterchief-build "$app_dir/python"
install -d -m 0750 -o rosterchief-build -g rosterchief-build "$data_dir/build"
install -d -m 0755 -o rosterchief -g rosterchief "$data_dir/media"
install -d -m 0700 -o rosterchief -g rosterchief "$data_dir/private_media"
install -d -m 0700 "$data_dir/checkpoints"
install -d -m 0700 -o caddy -g caddy /var/lib/caddy
ln -sfn /usr/bin/node-24 "$app_dir/bin/node"

scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
if [[ ! -x /usr/local/bin/uv ]]; then
    curl --fail --silent --show-error --location https://astral.sh/uv/install.sh --output "$scratch/uv.sh"
    env UV_UNMANAGED_INSTALL=/usr/local/bin sh "$scratch/uv.sh"
fi

if [[ -n $caddy_binary ]]; then
    install -m 0755 "$caddy_binary" "$scratch/caddy"
elif [[ ! -x /usr/local/bin/caddy ]]; then
    case "$(uname -m)" in
        x86_64) caddy_arch=amd64 ;;
        aarch64) caddy_arch=arm64 ;;
        *) echo "Unsupported CPU architecture for Caddy." >&2; exit 2 ;;
    esac
    curl --fail --silent --show-error --location \
        "https://caddyserver.com/api/download?os=linux&arch=$caddy_arch&p=github.com%2Fcaddy-dns%2Froute53" \
        --output "$scratch/caddy"
    chmod 0755 "$scratch/caddy"
fi
candidate=/usr/local/bin/caddy
if [[ -x $scratch/caddy ]]; then
    candidate=$scratch/caddy
fi
if ! "$candidate" list-modules | grep -Fx 'dns.providers.route53' >/dev/null; then
    echo "Caddy must include the Route 53 DNS module." >&2
    exit 1
fi
if [[ $candidate == "$scratch/caddy" ]]; then
    install -m 0755 "$candidate" /usr/local/bin/caddy
fi

pg_data=/var/lib/pgsql/data
if [[ ! -f $pg_data/PG_VERSION ]]; then
    postgresql-setup --initdb
fi
if ! grep -Fxq 'local all rosterchief peer' "$pg_data/pg_hba.conf"; then
    { printf '%s\n' 'local all rosterchief peer'; cat "$pg_data/pg_hba.conf"; } > "$scratch/pg_hba.conf"
    install -m 0600 -o postgres -g postgres "$scratch/pg_hba.conf" "$pg_data/pg_hba.conf"
    pg_reload=true
else
    pg_reload=false
fi
systemctl enable --now postgresql.service
pg() { runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 "$@"; }
if [[ -n $(pg -Atqc 'SHOW listen_addresses') ]]; then
    pg -qc "ALTER SYSTEM SET listen_addresses = ''"
    systemctl restart postgresql.service
elif [[ $pg_reload == true ]]; then
    systemctl reload postgresql.service
fi
if [[ $(pg -Atqc "SELECT 1 FROM pg_roles WHERE rolname = 'rosterchief'") != 1 ]]; then
    runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole rosterchief
fi
if [[ $(pg -Atqc "SELECT 1 FROM pg_database WHERE datname = 'rosterchief'") != 1 ]]; then
    runuser -u postgres -- createdb --owner=rosterchief rosterchief
fi

install -m 0644 /dev/stdin /etc/valkey/rosterchief.conf <<'VALKEY'
bind 127.0.0.1
protected-mode yes
appendonly yes
VALKEY
if ! grep -Fxq 'include /etc/valkey/rosterchief.conf' /etc/valkey/valkey.conf; then
    printf '\ninclude /etc/valkey/rosterchief.conf\n' >> /etc/valkey/valkey.conf
    systemctl enable valkey.service
    systemctl restart valkey.service
else
    systemctl enable --now valkey.service
fi

if [[ ! -f $config_dir/site.env ]]; then
    install -m 0644 /dev/stdin "$config_dir/site.env" <<SITE
ROSTERCHIEF_BASE_DOMAIN=$domain
PLATFORM_OWNER_EMAIL=$owner_email
CADDY_ACME_EMAIL=$owner_email
ROSTERCHIEF_DATABASE_NAME=rosterchief
SITE
fi
if [[ ! -f $config_dir/app.env ]]; then
    secret_key=$(openssl rand -hex 48)
    sed -e "s/@BASE_DOMAIN@/$domain/g" -e "s/@SECRET_KEY@/$secret_key/g" \
        "$source_dir/app.env.example" | install -m 0600 /dev/stdin "$config_dir/app.env"
fi
if [[ ! -f $config_dir/Caddyfile ]]; then
    install -m 0644 "$source_dir/Caddyfile" "$config_dir/Caddyfile"
fi

install -m 0644 "$source_dir"/systemd/* /etc/systemd/system/
install -m 0755 "$source_dir/health.sh" /usr/local/sbin/rosterchief-health
systemctl daemon-reload

echo "Host configured for $domain. Configure SMTP in $config_dir/app.env, then run deploy.sh."
