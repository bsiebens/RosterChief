#!/usr/bin/env bash
# Prepares an Amazon Linux 2023 host for one role. Safe to re-run. Normally started by `deploy/rc provision`.
#
#   full      PostgreSQL, Valkey, Django, Celery, both SvelteKit apps and Caddy on one host
#   backend   PostgreSQL, Valkey, Django, Celery, media and a private-network Caddy listener (no public TLS)
#   frontend  both SvelteKit apps and public Caddy; talks to a backend host over the private network
set -euo pipefail

usage() {
    cat >&2 << 'USAGE'
Usage: sudo bootstrap.sh --role full|backend|frontend --domain BASE_DOMAIN --owner-email EMAIL [options]

  --proxied true|false        Is the domain proxied by Cloudflare (orange cloud)? Default true.
  --access dns|ip             dns (default): a domain on Cloudflare. ip: no domain; reached as <ip>.sslip.io with Caddy's own certificate.
  --private-ip IP             backend: the private address Caddy listens on (required)
  --trusted-cidr CIDR         backend: the private network the frontend host(s) live in, e.g. 10.0.0.0/16 (required)
  --backend-url URL           frontend: the backend's private URL, e.g. http://10.0.1.5:8001 (required)
  --github-repo OWNER/REPO    where releases are downloaded from (written to deploy.env)
  --caddy-binary PATH         use this Caddy build instead of downloading one with the Cloudflare DNS module
  --profile standard|small    small: a 1 GB staging host. No Celery worker or beat (tasks run in the request), one gunicorn worker,
                              memory caps on the services, a trimmed PostgreSQL and Valkey. Default standard.
  --swap-mb N                 size of the swap file in MB, created when the host has no swap (0: none). Default 1024.

Secrets come from the environment, never the command line:
  CLOUDFLARE_API_TOKEN        full/frontend: Zone:Read + DNS:Edit on the zone, for the wildcard certificate
  GITHUB_TOKEN                read-only token able to read the repository's releases
USAGE
    exit 2
}

[[ $EUID == 0 ]] || usage
source /etc/os-release
if [[ ${ID:-} != amzn || ${VERSION_ID:-} != 2023 ]]; then
    echo "This bootstrap requires Amazon Linux 2023." >&2
    exit 2
fi

role='' domain='' owner_email='' proxied=true access=dns private_ip='' trusted_cidr='' backend_url='' github_repo='' caddy_binary='' profile=standard swap_mb=1024
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || usage
    case $1 in
        --role) role=$2 ;;
        --domain) domain=$2 ;;
        --owner-email) owner_email=$2 ;;
        --proxied) proxied=$2 ;;
        --access) access=$2 ;;
        --private-ip) private_ip=$2 ;;
        --trusted-cidr) trusted_cidr=$2 ;;
        --backend-url) backend_url=$2 ;;
        --github-repo) github_repo=$2 ;;
        --caddy-binary) caddy_binary=$2 ;;
        --profile) profile=$2 ;;
        --swap-mb) swap_mb=$2 ;;
        *) usage ;;
    esac
    shift 2
done

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$source_dir/common.sh"

rc_valid_role "$role" || usage
[[ $proxied == true || $proxied == false ]] || usage
[[ $access == dns || $access == ip ]] || usage
[[ $profile == standard || $profile == small ]] || usage
[[ $swap_mb =~ ^[0-9]+$ ]] || usage
[[ $access == dns || $proxied == false ]] || { echo "--access ip cannot be combined with the Cloudflare proxy." >&2; exit 2; }
if [[ ! $domain =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ || ! $owner_email =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z0-9]+$ ]]; then
    echo "Supply a lowercase public DNS domain and the platform owner's email address." >&2
    exit 2
fi
if [[ $role == backend ]]; then
    [[ $private_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ && $trusted_cidr =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "A backend host needs --private-ip and --trusted-cidr." >&2; exit 2; }
fi
if [[ $role == frontend ]]; then
    [[ $backend_url =~ ^http://[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "A frontend host needs --backend-url like http://10.0.1.5:8001 (plain HTTP, private network)." >&2; exit 2; }
fi
if [[ -n $github_repo && ! $github_repo =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "--github-repo must look like OWNER/REPO." >&2
    exit 2
fi

if [[ -f $RC_CONFIG/site.env ]]; then
    existing_role=$(grep -E '^ROSTERCHIEF_ROLE=' "$RC_CONFIG/site.env" | cut -d= -f2 || true)
    if ! grep -Fqx "ROSTERCHIEF_BASE_DOMAIN=$domain" "$RC_CONFIG/site.env" ||
       ! grep -Fqx "PLATFORM_OWNER_EMAIL=$owner_email" "$RC_CONFIG/site.env" ||
       [[ -n $existing_role && $existing_role != "$role" ]]; then
        echo "The host is already configured for another domain, owner or role." >&2
        exit 2
    fi
fi

# ---------------------------------------------------------------- swap
# Amazon Linux 2023 has none. Without it a memory spike freezes the host (SSH included) instead of slowing it down.
# Done first: installing packages and building the Python environment are memory-hungry too.
if [[ $swap_mb -gt 0 && -z $(swapon --show --noheadings) ]]; then
    if [[ ! -f /swapfile ]]; then
        dd if=/dev/zero of=/swapfile bs=1M count="$swap_mb" status=none  # dd, not fallocate: always works on XFS
        chmod 0600 /swapfile
        mkswap -q /swapfile
    fi
    swapon /swapfile
    grep -Eq '^/swapfile[[:space:]]' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
    echo "Swap: ${swap_mb} MB at /swapfile."
fi

# ---------------------------------------------------------------- packages, accounts, directories
packages=(git tar gzip openssl jq)
# Amazon Linux 2023 ships curl-minimal, which provides curl and conflicts with the full curl package: only install it when there is no curl.
command -v curl > /dev/null || packages+=(curl)
rc_has_backend "$role" && packages+=(postgresql16-server valkey pango fontconfig dejavu-sans-fonts)
rc_has_frontend "$role" && packages+=(nodejs24)
dnf install -y "${packages[@]}"
for account in rosterchief rosterchief-build rosterchief-frontend caddy; do
    if ! id "$account" > /dev/null 2>&1; then
        useradd --system --user-group --shell /sbin/nologin --no-create-home "$account"
    fi
done

install -d -m 0755 "$RC_APP" "$RC_APP/releases" "$RC_APP/bin" "$RC_CONFIG" "$RC_DATA" "$RC_LIB"
install -d -m 0700 -o caddy -g caddy /var/lib/caddy
install -d -m 0750 -o rosterchief-build -g rosterchief-build "$RC_DATA/build"
install -d -m 0700 "$RC_DATA/build/downloads"
if rc_has_backend "$role"; then
    install -d -m 0755 -o rosterchief-build -g rosterchief-build "$RC_APP/python"
    install -d -m 0755 -o rosterchief -g rosterchief "$RC_DATA/media"
    install -d -m 0700 -o rosterchief -g rosterchief "$RC_DATA/private_media"
    install -d -m 0700 "$RC_DATA/checkpoints"
fi
if rc_has_frontend "$role"; then
    ln -sfn /usr/bin/node-24 "$RC_APP/bin/node"
fi

scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
if rc_has_backend "$role" && [[ ! -x /usr/local/bin/uv ]]; then
    curl --fail --silent --show-error --location https://astral.sh/uv/install.sh --output "$scratch/uv.sh"
    env UV_UNMANAGED_INSTALL=/usr/local/bin sh "$scratch/uv.sh"
fi

# ---------------------------------------------------------------- Caddy (with the Cloudflare DNS module on public hosts)
needs_cloudflare=false
rc_has_frontend "$role" && needs_cloudflare=true
caddy_ok() { # does this binary have what the role needs?
    [[ -x $1 ]] || return 1
    [[ $needs_cloudflare == false ]] && return 0
    # Capture first: `caddy list-modules | grep -q` makes grep quit at the first match, caddy then dies of SIGPIPE and pipefail reports a failure.
    local modules
    modules=$("$1" list-modules 2>&1) || return 1
    grep -Fxq 'dns.providers.cloudflare' <<< "$modules"
}
if [[ -n $caddy_binary ]]; then
    install -m 0755 "$caddy_binary" "$scratch/caddy"
elif ! caddy_ok /usr/local/bin/caddy; then
    case "$(uname -m)" in
        x86_64) caddy_arch=amd64 ;;
        aarch64) caddy_arch=arm64 ;;
        *) echo "Unsupported CPU architecture for Caddy." >&2; exit 2 ;;
    esac
    curl --fail --silent --show-error --location \
        "https://caddyserver.com/api/download?os=linux&arch=$caddy_arch&p=github.com%2Fcaddy-dns%2Fcloudflare" \
        --output "$scratch/caddy"
    chmod 0755 "$scratch/caddy"
fi
if [[ -x $scratch/caddy ]]; then
    caddy_ok "$scratch/caddy" || { echo "Caddy must include the Cloudflare DNS module (dns.providers.cloudflare)." >&2; exit 1; }
    install -m 0755 "$scratch/caddy" /usr/local/bin/caddy
fi

# ---------------------------------------------------------------- PostgreSQL and Valkey (backend/full)
if rc_has_backend "$role"; then
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
    if [[ $profile == small ]]; then
        # A 1 GB host: a small cache and few connections (the web app runs one worker).
        if [[ $(pg -Atqc 'SHOW shared_buffers') != 48MB ]]; then
            pg -qc "ALTER SYSTEM SET shared_buffers = '48MB'"
            pg -qc "ALTER SYSTEM SET max_connections = 20"
            pg -qc "ALTER SYSTEM SET work_mem = '2MB'"
            pg -qc "ALTER SYSTEM SET maintenance_work_mem = '32MB'"
            pg -qc "ALTER SYSTEM SET effective_cache_size = '256MB'"
            systemctl restart postgresql.service
        fi
    fi
    if [[ $(pg -Atqc "SELECT 1 FROM pg_roles WHERE rolname = 'rosterchief'") != 1 ]]; then
        runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole rosterchief
    fi
    if [[ $(pg -Atqc "SELECT 1 FROM pg_database WHERE datname = 'rosterchief'") != 1 ]]; then
        runuser -u postgres -- createdb --owner=rosterchief rosterchief
    fi

    printf '%s\n' 'bind 127.0.0.1' 'protected-mode yes' > "$scratch/valkey.conf"
    if [[ $profile == small ]]; then
        # No Celery queue lives here on a small host: a bounded cache, and no append-only file (its rewrites fork the process).
        printf '%s\n' 'appendonly no' 'maxmemory 64mb' 'maxmemory-policy allkeys-lru' >> "$scratch/valkey.conf"
    else
        printf '%s\n' 'appendonly yes' >> "$scratch/valkey.conf"
    fi
    valkey_changed=false
    cmp -s "$scratch/valkey.conf" /etc/valkey/rosterchief.conf 2> /dev/null || valkey_changed=true
    install -m 0644 "$scratch/valkey.conf" /etc/valkey/rosterchief.conf
    if ! grep -Fxq 'include /etc/valkey/rosterchief.conf' /etc/valkey/valkey.conf; then
        printf '\ninclude /etc/valkey/rosterchief.conf\n' >> /etc/valkey/valkey.conf
        valkey_changed=true
    fi
    systemctl enable --now valkey.service
    if [[ $valkey_changed == true ]]; then
        systemctl restart valkey.service
    fi
fi

# ---------------------------------------------------------------- configuration files
[[ -f $RC_CONFIG/site.env ]] || install -m 0644 /dev/null "$RC_CONFIG/site.env"
rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_ROLE "$role"
rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_BASE_DOMAIN "$domain"
rc_set_kv "$RC_CONFIG/site.env" PLATFORM_OWNER_EMAIL "$owner_email"
rc_set_kv "$RC_CONFIG/site.env" CADDY_ACME_EMAIL "$owner_email"
rc_set_kv "$RC_CONFIG/site.env" CLOUDFLARE_PROXIED "$proxied"
rc_set_kv "$RC_CONFIG/site.env" ACCESS_MODE "$access"
rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_PROFILE "$profile"
case $role in
    full)
        grep -q '^ROSTERCHIEF_DATABASE_NAME=.\+' "$RC_CONFIG/site.env" || rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_DATABASE_NAME rosterchief
        rc_set_kv "$RC_CONFIG/site.env" BACKEND_URL http://127.0.0.1:8000
        ;;
    backend)
        grep -q '^ROSTERCHIEF_DATABASE_NAME=.\+' "$RC_CONFIG/site.env" || rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_DATABASE_NAME rosterchief
        rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_BACKEND_BIND "$private_ip"
        rc_set_kv "$RC_CONFIG/site.env" ROSTERCHIEF_TRUSTED_CIDR "$trusted_cidr"
        ;;
    frontend)
        rc_set_kv "$RC_CONFIG/site.env" BACKEND_URL "$backend_url"
        ;;
esac
chmod 0644 "$RC_CONFIG/site.env"

if rc_has_backend "$role" && [[ ! -f $RC_CONFIG/app.env ]]; then
    secret_key=$(openssl rand -hex 48)
    sed -e "s/@BASE_DOMAIN@/$domain/g" -e "s/@SECRET_KEY@/$secret_key/g" \
        "$source_dir/app.env.example" | install -m 0600 /dev/stdin "$RC_CONFIG/app.env"
fi

if rc_has_frontend "$role"; then
    [[ -f $RC_CONFIG/caddy.env ]] || install -m 0600 /dev/null "$RC_CONFIG/caddy.env"
    if [[ -n ${CLOUDFLARE_API_TOKEN:-} ]]; then
        rc_set_kv "$RC_CONFIG/caddy.env" CLOUDFLARE_API_TOKEN "$CLOUDFLARE_API_TOKEN"
    elif [[ $access == dns ]] && ! grep -q '^CLOUDFLARE_API_TOKEN=.\+' "$RC_CONFIG/caddy.env"; then
        echo "WARNING: CLOUDFLARE_API_TOKEN is not set in $RC_CONFIG/caddy.env. Caddy cannot obtain certificates until it is." >&2
    fi
fi
if [[ -n ${GITHUB_TOKEN:-} || -n $github_repo ]]; then
    [[ -f $RC_CONFIG/deploy.env ]] || install -m 0600 /dev/null "$RC_CONFIG/deploy.env"
    [[ -z $github_repo ]] || rc_set_kv "$RC_CONFIG/deploy.env" GITHUB_REPO "$github_repo"
    [[ -z ${GITHUB_TOKEN:-} ]] || rc_set_kv "$RC_CONFIG/deploy.env" GITHUB_TOKEN "$GITHUB_TOKEN"
fi
if [[ ! -f $RC_CONFIG/deploy.env ]] || ! grep -q '^GITHUB_TOKEN=.\+' "$RC_CONFIG/deploy.env"; then
    echo "WARNING: $RC_CONFIG/deploy.env has no GITHUB_REPO/GITHUB_TOKEN. Releases can only be installed with --from-dir." >&2
fi

# ---------------------------------------------------------------- scripts, units, Caddyfile
[[ -f $RC_CONFIG/cloudflare-ips.caddy ]] || install -m 0644 /dev/null "$RC_CONFIG/cloudflare-ips.caddy"
rc_install_host_files "$source_dir" "$role"
if rc_has_frontend "$role"; then
    # Writes the trusted-proxy snippet for the chosen mode (fetches Cloudflare's ranges when proxied).
    /usr/local/sbin/rosterchief-cloudflare-ips set "$([[ $proxied == true ]] && echo on || echo off)"
fi
rc_validate_caddyfile "$RC_CONFIG/Caddyfile" || { echo "The Caddyfile does not validate." >&2; exit 1; }

echo "Host configured as '$role' for $domain (Cloudflare proxy: $proxied, profile: $profile)."
echo "Next: install a release with 'deploy/rc deploy <environment> vX.Y.Z', or on this host 'sudo rosterchief-deploy release vX.Y.Z'."
