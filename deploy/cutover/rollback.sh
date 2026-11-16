#!/usr/bin/env bash
# shellcheck disable=SC2154

# Puts the old code back on main and development after cutover.sh. Run from anywhere.
#
#   rollback.sh --out DIR --mode revert [--branches main|development|both] [--yes]
#       Adds a revert commit for the cutover merge. Non-destructive: history only grows, nothing is forced. To bring the
#       new code back later, revert that revert commit (git revert <the revert>), as with any reverted merge.
#   rollback.sh --out DIR --mode reset [--branches ...] [--yes]
#       Moves the branch(es) back to the exact commits recorded before the cutover (force-push with a lease). Rewrites the
#       branch, so anything pushed after the cutover is dropped from it. Needs branch protection to allow it for a moment.
#
# --out is the directory of the 'prepare' run (it holds state.env). Last resort: restore from <out>/old-repo.bundle or <out>/backup.git.
set -euo pipefail

die() { printf 'rollback: %s\n' "$*" >&2; exit 1; }
out='' mode='' branches=both assume_yes=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --out) out=${2:?}; shift ;;
        --mode) mode=${2:?}; shift ;;
        --branches) branches=${2:?}; shift ;;
        --yes) assume_yes=1 ;;
        *) die "Unknown option $1" ;;
    esac
    shift
done
[[ $mode == revert || $mode == reset ]] || die "Choose --mode revert or --mode reset."
[[ $branches == main || $branches == development || $branches == both ]] || die "--branches is main, development or both."
[[ -f $out/state.env ]] || die "No $out/state.env."
# shellcheck disable=SC1091
source "$out/state.env"

confirm() {
    [[ $assume_yes == 1 ]] && return 0
    local answer
    printf '%s Type "%s" to continue: ' "$1" "$2"
    read -r answer
    [[ $answer == "$2" ]]
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone -q "$old" "$work/repo"
repo=$work/repo
git -C "$repo" config user.name > /dev/null || git -C "$repo" config user.name "rollback"
git -C "$repo" config user.email > /dev/null || git -C "$repo" config user.email "rollback@localhost"

rollback_branch() { # NAME RECORDED_SHA
    local name=$1 recorded=$2 current merge
    current=$(git -C "$repo" rev-parse "origin/$name")
    if [[ $current == "$recorded" ]]; then
        echo "$name is already at the recorded pre-cutover commit."
        return 0
    fi
    git -C "$repo" merge-base --is-ancestor "$recorded" "$current" || die "$name no longer contains the recorded commit $recorded."
    if [[ $mode == reset ]]; then
        echo "$name: $(git -C "$repo" rev-parse --short "$current") -> $(git -C "$repo" rev-parse --short "$recorded") (forced, with a lease)."
        confirm "Force-push $name?" "reset" || die "Cancelled."
        git -C "$repo" push --force-with-lease="refs/heads/$name:$current" origin "$recorded:refs/heads/$name"
        return 0
    fi
    # The cutover merge is the first-parent commit whose first parent is the recorded pre-cutover tip.
    merge=$(git -C "$repo" log "origin/$name" --first-parent --merges --format='%H %P' | awk -v want="$recorded" '$2 == want { print $1 }' | tail -n 1)
    [[ -n $merge ]] || die "Could not find the cutover merge on $name (first parent $recorded). Use --mode reset."
    echo "$name: reverting cutover merge $(git -C "$repo" rev-parse --short "$merge") (non-destructive)."
    confirm "Push the revert to $name?" "revert" || die "Cancelled."
    git -C "$repo" checkout -q --no-track -B "rollback-$name" "origin/$name"
    git -C "$repo" revert --no-edit -m 1 "$merge" > /dev/null
    git -C "$repo" push origin "HEAD:refs/heads/$name"
    if [[ $(git -C "$repo" rev-parse "HEAD^{tree}") == "$(git -C "$repo" rev-parse "$recorded^{tree}")" ]]; then
        echo "$name now has exactly the old code's files."
    else
        echo "$name was reverted, but later commits changed it: its files differ from the old code's. Review it."
    fi
}

[[ $branches == development ]] || rollback_branch "$main" "$old_main_sha"
[[ $branches == main ]] || rollback_branch "$dev" "$old_dev_sha"
echo "Done. The new code is still on $cutover and in the history; legacy/* branches and legacy-final-* tags are untouched."
