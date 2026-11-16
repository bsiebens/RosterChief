# shellcheck shell=bash
# How rc reaches a server. One primitive: rc_run INDEX < script. The script is plain bash text, sent over
# SSH (stdin to `sudo bash -s`) or AWS Systems Manager Run Command (AWS-RunShellScript), and its output and
# exit status come back. Secrets go over SSH only: SSM keeps command text in its history.

rc_run() {
    local i=$1 script
    script=$(cat)
    if [[ $RC_DRY_RUN == 1 ]]; then
        printf '\033[2m[dry-run] %s (%s %s)\033[0m\n' "${H_NAME[i]}" "${H_TRANSPORT[i]}" "${H_TARGET[i]}"
        printf '%s\n' "$script" | sed -E 's/(export (CLOUDFLARE_API_TOKEN|GITHUB_TOKEN)=).*/\1<redacted>/' | cut -c1-200 | sed 's/^/    | /'
        return 0
    fi
    case ${H_TRANSPORT[i]} in
        ssh) rc_run_ssh "$i" "$script" ;;
        ssm) rc_run_ssm "$i" "$script" ;;
    esac
}

rc_run_ssh() {
    local opts=()
    # shellcheck disable=SC2206
    opts=(${RC_SSH_OPTS:-})
    printf '%s\n' "$2" | ssh "${opts[@]+"${opts[@]}"}" -o BatchMode=yes -o ConnectTimeout=15 "${H_TARGET[$1]}" 'sudo bash -s'
}

rc_run_ssm() {
    local i=$1 script=$2 region target id params status out err
    local aws_args=()
    region=$(host_opt "$i" region)
    [[ -z $region ]] || aws_args=(--region "$region")
    target=${H_TARGET[i]}
    command -v aws > /dev/null || die "The aws CLI is required for ssm hosts."
    params=$(printf 'bash -s <<'"'"'RC_REMOTE_EOF'"'"'\n%s\nRC_REMOTE_EOF\n' "$script" |
        python3 -c 'import json, sys; print(json.dumps({"commands": [sys.stdin.read()], "executionTimeout": ["3600"]}))')
    ((${#params} < 60000)) || die "The command for ${H_NAME[i]} is too large for SSM. Use ssh for this action."
    id=$(aws ssm send-command "${aws_args[@]+"${aws_args[@]}"}" --instance-ids "$target" --document-name AWS-RunShellScript \
        --comment "rosterchief rc" --parameters "$params" --query Command.CommandId --output text)
    status=Pending
    while [[ $status == Pending || $status == InProgress || $status == Delayed ]]; do
        sleep 3
        status=$(aws ssm get-command-invocation "${aws_args[@]+"${aws_args[@]}"}" --command-id "$id" --instance-id "$target" --query Status --output text 2> /dev/null || echo Pending)
    done
    out=$(aws ssm get-command-invocation "${aws_args[@]+"${aws_args[@]}"}" --command-id "$id" --instance-id "$target" --query StandardOutputContent --output text)
    err=$(aws ssm get-command-invocation "${aws_args[@]+"${aws_args[@]}"}" --command-id "$id" --instance-id "$target" --query StandardErrorContent --output text)
    [[ -z $out || $out == None ]] || printf '%s\n' "$out"
    [[ -z $err || $err == None ]] || printf '%s\n' "$err" >&2
    [[ $status == Success ]] || { warn "SSM command $id ended as $status (SSM output is truncated at 24,000 characters; see the Run Command console for the rest)."; return 1; }
}
