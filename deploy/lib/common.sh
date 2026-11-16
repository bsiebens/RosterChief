# shellcheck shell=bash disable=SC2034
# Helpers for deploy/rc. Runs on the operator's machine, so it sticks to bash 3.2 (the macOS default):
# no associative arrays, no mapfile, no ${var,,}.

RC_DRY_RUN=${RC_DRY_RUN:-0}
RC_ASSUME_YES=${RC_ASSUME_YES:-0}

die() { printf 'rc: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf 'rc: warning: %s\n' "$*" >&2; }

# confirm "Question?" [--typed WORD]: asks the operator, or passes with --yes. A typed word guards production.
confirm() {
    local question=$1 word=${2:-} answer
    if [[ $RC_ASSUME_YES == 1 || $RC_DRY_RUN == 1 ]]; then return 0; fi
    if [[ -n $word ]]; then
        printf '%s Type "%s" to continue: ' "$question" "$word"
        read -r answer
        [[ $answer == "$word" ]]
    else
        printf '%s [y/N] ' "$question"
        read -r answer
        [[ $answer == y || $answer == Y || $answer == yes ]]
    fi
}

valid_version_tag() { [[ $1 =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]]; }

# ----------------------------------------------------------------------------- environments
# An environment file defines BASE_DOMAIN, OWNER_EMAIL, GITHUB_REPO, optionally CLOUDFLARE_PROXIED,
# TRUSTED_CIDR, PROMOTE_FROM, and the HOSTS array. Secrets live in <env>.secrets (never in git).
H_NAME=() H_ROLE=() H_TRANSPORT=() H_TARGET=() H_OPTS=()
ENV_NAME=

load_env() {
    local name=$1 file secrets spec n r t g o i
    [[ $name =~ ^[a-z0-9-]+$ ]] || die "Environment names are lowercase letters, digits and dashes."
    file=$RC_DIR/environments/$name.env
    secrets=$RC_DIR/environments/$name.secrets
    [[ -f $file ]] || die "No $file. Copy deploy/environments/$name.env.example and fill it in."
    HOSTS=() BASE_DOMAIN='' OWNER_EMAIL='' GITHUB_REPO='' CLOUDFLARE_PROXIED=true TRUSTED_CIDR='' PROMOTE_FROM=''
    # shellcheck disable=SC1090
    source "$file"
    if [[ -f $secrets ]]; then
        [[ $(stat -f '%Lp' "$secrets" 2> /dev/null || stat -c '%a' "$secrets") =~ ^[0-7]00$ ]] || die "$secrets must not be readable by others (chmod 600)."
        # shellcheck disable=SC1090
        source "$secrets"
    fi
    ENV_NAME=$name
    [[ -n $BASE_DOMAIN && -n $OWNER_EMAIL && -n $GITHUB_REPO ]] || die "$file must set BASE_DOMAIN, OWNER_EMAIL and GITHUB_REPO."
    [[ $CLOUDFLARE_PROXIED == true || $CLOUDFLARE_PROXIED == false ]] || die "CLOUDFLARE_PROXIED must be true or false."
    ((${#HOSTS[@]} > 0)) || die "$file lists no HOSTS."
    H_NAME=() H_ROLE=() H_TRANSPORT=() H_TARGET=() H_OPTS=()
    for spec in "${HOSTS[@]}"; do
        n='' r='' t='' g='' o=''
        read -r n r t g o <<< "$spec"
        [[ $n =~ ^[a-z0-9-]+$ ]] || die "Bad host name in '$spec'."
        [[ $r == full || $r == backend || $r == frontend ]] || die "Host '$n': role must be full, backend or frontend."
        [[ $t == ssh || $t == ssm ]] || die "Host '$n': transport must be ssh or ssm."
        [[ -n $g ]] || die "Host '$n' has no target (user@host for ssh, instance id for ssm)."
        for ((i = 0; i < ${#H_NAME[@]}; i++)); do
            [[ ${H_NAME[i]} != "$n" ]] || die "Host name '$n' is listed twice."
        done
        H_NAME+=("$n") H_ROLE+=("$r") H_TRANSPORT+=("$t") H_TARGET+=("$g") H_OPTS+=("$o")
    done
}

# host_opt INDEX KEY: the value of key=value in the host's trailing options, or empty.
host_opt() {
    local pair
    for pair in ${H_OPTS[$1]}; do
        if [[ $pair == "$2="* ]]; then printf '%s' "${pair#*=}"; return 0; fi
    done
    return 0
}

# Prints the indexes of the hosts to act on, backend-carrying hosts first (migrations before the frontends
# that depend on them). Filters: $1 = role (all|full|backend|frontend), $2 = host name or empty.
select_hosts() {
    local role=${1:-all} only=${2:-} i pass found=0
    [[ $role == all || $role == full || $role == backend || $role == frontend ]] || die "--role must be all, full, backend or frontend."
    for pass in backend frontend; do
        for ((i = 0; i < ${#H_NAME[@]}; i++)); do
            [[ -z $only || ${H_NAME[i]} == "$only" ]] || continue
            [[ $role == all || ${H_ROLE[i]} == "$role" ]] || continue
            if [[ $pass == backend && ${H_ROLE[i]} != frontend ]] || [[ $pass == frontend && ${H_ROLE[i]} == frontend ]]; then
                printf '%s\n' "$i"
                found=1
            fi
        done
    done
    [[ $found == 1 ]] || die "No host matches (role=$role${only:+, host=$only}) in environment $ENV_NAME."
}

# The private URL frontend hosts use to reach the backend (the first backend host's private_ip).
backend_url() {
    local i ip
    for ((i = 0; i < ${#H_NAME[@]}; i++)); do
        if [[ ${H_ROLE[i]} == backend ]]; then
            ip=$(host_opt "$i" private_ip)
            [[ -n $ip ]] || die "Backend host '${H_NAME[i]}' needs private_ip=… in its options."
            printf 'http://%s:8001' "$ip"
            return 0
        fi
    done
    die "A frontend host needs a backend host in the same environment."
}
