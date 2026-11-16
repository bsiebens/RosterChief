#!/usr/bin/env bash
# Replaces the code of an existing GitHub repository with a new codebase while keeping BOTH histories.
#
#   cutover.sh scan    --new PATH [--new-branch B]            look for secrets and private files in the history about to be published
#   cutover.sh prepare --old URL --new PATH [options]         back up the old repo and build the cutover branches locally (pushes nothing)
#   cutover.sh push    --out DIR [--yes]                      publish the legacy refs and the cutover branch (never forced)
#   cutover.sh finish  --out DIR [--yes]                      after the cutover PR is merged: bring the old development branch across
#
# How it keeps the old code: the new tree arrives as a MERGE COMMIT on top of the old main (parents: old main, new history), so every
# old commit stays an ancestor and nothing is force-pushed. Before that, `legacy/<branch>` branches and `legacy-final-<branch>` tags
# are pushed at the old tips, and a full mirror plus a bundle of the old repository is written to the output directory.
# Undo it with rollback.sh. See docs/repo-cutover.md.
set -euo pipefail

usage() {
    sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    cat >&2 << 'USAGE'

Options:
  --old URL|PATH       the repository to keep (the GitHub URL, e.g. git@github.com:owner/Repo.git; a local path works for rehearsals)
  --new PATH           a checkout of the new codebase (its branch must be committed)
  --new-branch NAME    the new codebase's branch to publish (default: development)
  --main NAME          the old production branch (default: main)
  --dev NAME           the old integration branch (default: development)
  --cutover NAME       name of the branch to push for the pull request (default: v2-cutover)
  --out DIR            where the backup, state file and working clone live (default: ./repo-cutover-<timestamp>)
  --yes                do not ask for confirmation
USAGE
    exit 2
}

die() { printf 'cutover: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[1m==> %s\033[0m\n' "$*"; }

cmd=${1:-}
[[ -n $cmd ]] || usage
shift
old='' new='' new_branch=development main=main dev=development cutover=v2-cutover out='' assume_yes=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --old) old=${2:?}; shift ;;
        --new) new=${2:?}; shift ;;
        --new-branch) new_branch=${2:?}; shift ;;
        --main) main=${2:?}; shift ;;
        --dev) dev=${2:?}; shift ;;
        --cutover) cutover=${2:?}; shift ;;
        --out) out=${2:?}; shift ;;
        --yes) assume_yes=1 ;;
        *) usage ;;
    esac
    shift
done

confirm() {
    [[ $assume_yes == 1 ]] && return 0
    local answer
    printf '%s Type "%s" to continue: ' "$1" "$2"
    read -r answer
    [[ $answer == "$2" ]]
}

# ------------------------------------------------------------------------------------------------- scan
scan() {
    local repo=$1 ref=$2 files hits status=0
    info "Scanning $ref for private files and secrets (heuristic: not a substitute for a real scanner such as gitleaks)"
    files=$(git -C "$repo" log "$ref" --diff-filter=A --name-only --format= | sort -u |
        grep -E '(^|/)(\.env(\..*)?|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|[^/]*\.sqlite3(-journal)?|[^/]*\.bak[^/]*|[^/]*\.p12|[^/]*\.pfx)$' |
        grep -vE '\.(example|sample)$' || true)
    if [[ -n $files ]]; then
        status=1
        printf 'Files that look private were added at some point in the history:\n%s\n' "$files" | sed 's/^/  /'
    fi
    hits=$(git -C "$repo" log "$ref" -p --format= 2> /dev/null |
        grep -nE 'AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|re_[A-Za-z0-9]{24,}|sk_live_[A-Za-z0-9]{16,}' | head -20 || true)
    if [[ -n $hits ]]; then
        status=1
        printf 'Lines that look like credentials (line numbers are within the full patch output):\n%s\n' "$(printf '%s\n' "$hits" | cut -c1-120)" | sed 's/^/  /'
    fi
    [[ $status == 0 ]] && echo "Nothing suspicious found."
    return $status
}

require_new() {
    [[ -n $new ]] || usage
    git -C "$new" rev-parse --git-dir > /dev/null 2>&1 || die "--new is not a git checkout: $new"
    git -C "$new" rev-parse --verify --quiet "refs/heads/$new_branch" > /dev/null || die "Branch $new_branch does not exist in $new."
    new=$(cd "$new" && pwd -P)  # absolute: it becomes a remote of a clone elsewhere, where a relative path would mean something else
}

# ------------------------------------------------------------------------------------------------- shared
# graft NAME BASE PARENT TREE MESSAGE: branch NAME = BASE + a merge commit whose second parent is PARENT and whose tree is exactly TREE's.
graft() {
    local name=$1 base=$2 parent=$3 tree=$4 message=$5
    git -C "$work" checkout -q --no-track -B "$name" "$base"
    git -C "$work" merge -q --allow-unrelated-histories -s ours --no-commit "$parent" > /dev/null 2>&1
    git -C "$work" read-tree --reset -u "$tree"
    git -C "$work" commit -q -m "$message"
    [[ $(git -C "$work" rev-parse "HEAD^{tree}") == "$(git -C "$work" rev-parse "$tree^{tree}")" ]] || die "$name: the tree is not the new code's tree."
    [[ $(git -C "$work" rev-list --parents -n 1 HEAD | wc -w | tr -d ' ') == 3 ]] || die "$name: expected a merge commit with two parents."
    git -C "$work" merge-base --is-ancestor "$base" HEAD || die "$name: the old history is not an ancestor."
    git -C "$work" merge-base --is-ancestor "$parent" HEAD || die "$name: the new history is not an ancestor."
}

load_state() {
    [[ -n $out && -f $out/state.env ]] || die "Give --out DIR of a previous 'prepare' run."
    # shellcheck disable=SC1091
    source "$out/state.env"
    work=$out/work
    [[ -d $work ]] || die "The working clone $work is missing."
}

# ------------------------------------------------------------------------------------------------- prepare
prepare() {
    require_new
    [[ -n $old ]] || usage
    [[ -n $out ]] || out=$PWD/repo-cutover-$(date +%Y%m%d-%H%M%S)
    [[ ! -e $out ]] || die "$out already exists."
    [[ -z $(git -C "$new" status --porcelain --untracked-files=no) ]] || die "$new has uncommitted changes. Commit them first: only committed work is published."
    mkdir -p "$out"
    out=$(cd "$out" && pwd)
    work=$out/work

    info "Backing up the old repository"
    git clone -q --mirror "$old" "$out/backup.git"
    git -C "$out/backup.git" bundle create "$out/old-repo.bundle" --all > /dev/null 2>&1
    git bundle verify "$out/old-repo.bundle" > /dev/null 2>&1 || die "The bundle did not verify."
    old_main_sha=$(git -C "$out/backup.git" rev-parse --verify "refs/heads/$main^{commit}") || die "The old repository has no branch $main."
    old_dev_sha=$(git -C "$out/backup.git" rev-parse --verify --quiet "refs/heads/$dev^{commit}" || true)
    [[ -n $old_dev_sha ]] || die "The old repository has no branch $dev (use --dev)."
    echo "  mirror:  $out/backup.git"
    echo "  bundle:  $out/old-repo.bundle   (copy this somewhere off this machine)"
    echo "  $main at $old_main_sha, $dev at $old_dev_sha"

    info "Looking at what will be published"
    git -C "$new" ls-tree -r --name-only "$new_branch" | grep -E '^\.github/' | sed 's/^/  workflow or GitHub config: /' || echo "  (no .github files)"
    scan "$new" "$new_branch" || echo "  Review the findings above before running 'push'."

    info "Building the cutover branches (nothing is pushed)"
    git clone -q "$out/backup.git" "$work"
    git -C "$work" remote set-url origin "$old"
    git -C "$work" remote set-url --push origin "no-push://disabled-until-the-push-step"
    git -C "$work" remote add v2 "$new"
    git -C "$work" fetch -q --no-tags v2 "+refs/heads/$new_branch:refs/remotes/v2/$new_branch"
    new_sha=$(git -C "$work" rev-parse "refs/remotes/v2/$new_branch")
    [[ $new_sha == "$(git -C "$new" rev-parse "refs/heads/$new_branch")" ]] || die "Fetched the wrong commit for the new codebase ($new_sha)."
    for old_branch in "$main" "$dev"; do
        if git -C "$work" merge-base --is-ancestor "$new_sha" "origin/$old_branch" || [[ $(git -C "$work" rev-parse "$new_sha^{tree}") == "$(git -C "$work" rev-parse "origin/$old_branch^{tree}")" ]]; then
            die "The new codebase's $new_branch is already part of (or identical to) the old repository's $old_branch. Is --new the right checkout?"
        fi
    done
    echo "  new codebase: $new_branch at $new_sha ($(git -C "$work" rev-list --count "$new_sha") commits)"
    git -C "$work" branch --no-track "legacy/$main" "origin/$main"
    git -C "$work" branch --no-track "legacy/$dev" "origin/$dev"
    git -C "$work" tag -a "legacy-final-$main" -m "Last state of $main before the new codebase replaced it" "origin/$main"
    git -C "$work" tag -a "legacy-final-$dev" -m "Last state of $dev before the new codebase replaced it" "origin/$dev"
    graft "$cutover" "origin/$main" "refs/remotes/v2/$new_branch" "refs/remotes/v2/$new_branch" "Replace the codebase with the new RosterChief

The tree is the new codebase's $new_branch; both histories are kept (first parent: the old $main, second parent: the new history).
The old code remains at legacy/$main, legacy/$dev and the legacy-final-* tags."
    cutover_sha=$(git -C "$work" rev-parse HEAD)
    {
        printf 'old=%q\nmain=%q\ndev=%q\nnew_branch=%q\ncutover=%q\n' "$old" "$main" "$dev" "$new_branch" "$cutover"
        printf 'old_main_sha=%q\nold_dev_sha=%q\nnew_sha=%q\ncutover_sha=%q\n' "$old_main_sha" "$old_dev_sha" "$new_sha" "$cutover_sha"
    } > "$out/state.env"
    git -C "$work" checkout -q "$cutover"
    echo
    echo "Ready. $cutover = $cutover_sha (tree identical to the new codebase)."
    echo "Next:  $0 push --out $out"
}

# ------------------------------------------------------------------------------------------------- push
push() {
    load_state
    echo "About to push to: $old"
    echo "  branches legacy/$main, legacy/$dev; tags legacy-final-$main, legacy-final-$dev; branch $cutover"
    echo "  (none of this touches $main or $dev, and nothing is forced)"
    confirm "Push?" "push" || die "Cancelled."
    git -C "$work" config --unset-all remote.origin.pushurl
    git -C "$work" push origin "refs/heads/legacy/$main" "refs/heads/legacy/$dev" "refs/tags/legacy-final-$main" "refs/tags/legacy-final-$dev"
    git -C "$work" push origin "refs/heads/$cutover:refs/heads/$cutover"
    git -C "$work" remote set-url --push origin "no-push://disabled-until-the-push-step"
    echo
    echo "Pushed. Now:"
    echo "  1. Protect legacy/$main and legacy/$dev on GitHub (no deletion, no force-push)."
    echo "  2. Open a pull request $cutover -> $main and merge it with \"Create a merge commit\" (not squash or rebase)."
    echo "  3. $0 finish --out $out"
}

# ------------------------------------------------------------------------------------------------- finish
finish() {
    load_state
    git -C "$work" fetch -q --no-tags origin "+refs/heads/*:refs/remotes/origin/*"
    git -C "$work" merge-base --is-ancestor "$old_main_sha" "origin/$main" || die "origin/$main no longer contains the old history."
    git -C "$work" merge-base --is-ancestor "$new_sha" "origin/$main" || die "origin/$main does not contain the new history: the PR was squashed or rebased. Use rollback.sh, then merge with a merge commit."
    [[ $(git -C "$work" rev-parse "origin/$main^{tree}") == "$(git -C "$work" rev-parse "$new_sha^{tree}")" ]] || die "origin/$main's tree is not the new codebase's tree (has it changed since the merge?)."
    graft "${cutover}-development" "origin/$dev" "origin/$main" "origin/$main" "Bring $dev onto the new codebase

First parent: the old $dev, second parent: $main after the cutover."
    echo "$dev will be fast-forwarded from $(git -C "$work" rev-parse --short "origin/$dev") to $(git -C "$work" rev-parse --short HEAD) on $old (not forced)."
    confirm "Push $dev?" "push" || die "Cancelled."
    git -C "$work" config --unset-all remote.origin.pushurl
    git -C "$work" push origin "HEAD:refs/heads/$dev"
    git -C "$work" remote set-url --push origin "no-push://disabled-until-the-push-step"
    echo "Done. $main and $dev carry the new codebase; the old code is at legacy/$main, legacy/$dev and the legacy-final-* tags."
    echo "Remaining by hand: required status checks, stale Dependabot branches, old Actions secrets (docs/repo-cutover.md)."
}

case $cmd in
    scan) require_new; scan "$new" "$new_branch" ;;
    prepare) prepare ;;
    push) push ;;
    finish) finish ;;
    *) usage ;;
esac
