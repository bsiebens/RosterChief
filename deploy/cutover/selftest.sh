#!/usr/bin/env bash
# shellcheck disable=SC2154

# Rehearses cutover.sh and rollback.sh end to end on throwaway repositories (a fake "GitHub" bare repo, an old codebase with a gitflow
# layout and tags, and a new codebase with unrelated history). Touches nothing outside a temporary directory. Run: bash deploy/cutover/selftest.sh
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export GIT_AUTHOR_NAME=selftest GIT_AUTHOR_EMAIL=selftest@example.com GIT_COMMITTER_NAME=selftest GIT_COMMITTER_EMAIL=selftest@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok: %s\n' "$*"; }
tree() { git -C "$1" rev-parse "$2^{tree}"; }
commit_file() { # repo path content message
    mkdir -p "$(dirname "$1/$2")"
    printf '%s\n' "$3" > "$1/$2"
    git -C "$1" add -A
    git -C "$1" commit -q -m "$4"
}

# ---- the old codebase: gitflow, tags, its own CI workflow that publishes images
old=$tmp/old
git init -q -b main "$old"
commit_file "$old" README.md "old app" "old: first"
commit_file "$old" .github/workflows/build-and-push.yml "name: old CI" "old: ci"
commit_file "$old" app/old_only.py "print('old')" "old: code"
git -C "$old" tag v0.1.0
git -C "$old" checkout -q -b development
commit_file "$old" app/dev_work.py "print('dev')" "old: development work"
git -C "$old" checkout -q main
git init -q --bare "$tmp/seed.git"
git -C "$old" push -q "$tmp/seed.git" --all
git -C "$old" push -q "$tmp/seed.git" --tags

# ---- the new codebase: unrelated history, different files, a different workflow
new=$tmp/new
git init -q -b development "$new"
commit_file "$new" manage.py "new app" "new: first"
commit_file "$new" .github/workflows/ci.yml "name: new CI" "new: ci"
commit_file "$new" deploy/rc "#!/bin/sh" "new: deploy"

fresh_github() { rm -rf "$1" && git clone -q --bare "$tmp/seed.git" "$1"; }
cutover() { bash "$here/cutover.sh" "$@" > "$tmp/last.log" 2>&1 || { cat "$tmp/last.log" >&2; fail "cutover.sh $1 failed"; }; }

echo "Scenario 1: merge commit, finish, revert rollback, reset rollback"
github=$tmp/github1.git
fresh_github "$github"
old_main=$(git -C "$github" rev-parse refs/heads/main)
old_dev=$(git -C "$github" rev-parse refs/heads/development)
out=$tmp/out1
cutover prepare --old "$github" --new "$new" --out "$out"
[[ $(git -C "$github" rev-parse refs/heads/main) == "$old_main" ]] || fail "prepare changed main on the remote"
git -C "$github" rev-parse --verify --quiet refs/heads/v2-cutover > /dev/null && fail "prepare pushed the cutover branch"
git -C "$out/work" push origin main > /dev/null 2>&1 && fail "pushing from the working clone was not blocked before the push step"
ok "prepare pushes nothing and blocks accidental pushes"
git bundle verify "$out/old-repo.bundle" > /dev/null 2>&1 || fail "bundle does not verify"
ok "bundle and mirror written"

cutover push --out "$out" --yes
[[ $(git -C "$github" rev-parse refs/heads/main) == "$old_main" ]] || fail "push moved main"
[[ $(git -C "$github" rev-parse refs/heads/legacy/main) == "$old_main" ]] || fail "legacy/main is not at the old main"
[[ $(git -C "$github" rev-parse refs/heads/legacy/development) == "$old_dev" ]] || fail "legacy/development is not at the old development"
git -C "$github" rev-parse --verify --quiet refs/tags/legacy-final-main > /dev/null || fail "legacy-final-main tag missing"
ok "legacy branches and tags pushed, main untouched"

pr=$tmp/pr1
git clone -q "$github" "$pr"
git -C "$pr" checkout -q main
git -C "$pr" merge -q --no-ff origin/v2-cutover -m "Merge pull request #1 from v2-cutover" > /dev/null
git -C "$pr" push -q origin main
cutover finish --out "$out" --yes

new_tree=$(tree "$new" development)
for branch in main development; do
    [[ $(tree "$github" "refs/heads/$branch") == "$new_tree" ]] || fail "$branch does not have the new tree"
done
git -C "$github" ls-tree -r --name-only refs/heads/main | grep -q 'build-and-push.yml' && fail "the old workflow is still on main"
git -C "$github" ls-tree -r --name-only refs/heads/main | grep -q '^app/old_only.py' && fail "old files remain on main"
git -C "$github" merge-base --is-ancestor "$old_main" refs/heads/main || fail "old main history lost from main"
git -C "$github" merge-base --is-ancestor "$old_dev" refs/heads/development || fail "old development history lost"
git -C "$github" merge-base --is-ancestor "$(git -C "$new" rev-parse development)" refs/heads/main || fail "new history not in main"
git -C "$github" merge-base --is-ancestor v0.1.0 refs/heads/main || fail "old tag not reachable from main"
ok "main and development have the new tree and contain both histories"

bash "$here/rollback.sh" --out "$out" --mode revert --yes > "$tmp/last.log" 2>&1 || { cat "$tmp/last.log" >&2; fail "revert rollback failed"; }
[[ $(tree "$github" refs/heads/main) == "$(tree "$github" "$old_main")" ]] || fail "revert did not restore the old files on main"
[[ $(tree "$github" refs/heads/development) == "$(tree "$github" "$old_dev")" ]] || fail "revert did not restore the old files on development"
git -C "$github" merge-base --is-ancestor "$(git -C "$new" rev-parse development)" refs/heads/main || fail "revert dropped the new history"
ok "revert rollback restores the old files and keeps the history (no force)"

bash "$here/rollback.sh" --out "$out" --mode reset --yes > "$tmp/last.log" 2>&1 || { cat "$tmp/last.log" >&2; fail "reset rollback failed"; }
[[ $(git -C "$github" rev-parse refs/heads/main) == "$old_main" ]] || fail "reset did not return main to the recorded commit"
[[ $(git -C "$github" rev-parse refs/heads/development) == "$old_dev" ]] || fail "reset did not return development to the recorded commit"
ok "reset rollback returns both branches to the exact recorded commits"

echo "Scenario 2: a squash-merged pull request is refused"
github=$tmp/github2.git
fresh_github "$github"
out=$tmp/out2
cutover prepare --old "$github" --new "$new" --out "$out"
cutover push --out "$out" --yes
pr=$tmp/pr2
git clone -q "$github" "$pr"
git -C "$pr" checkout -q main
git -C "$pr" merge -q --squash origin/v2-cutover > /dev/null 2>&1 || true
git -C "$pr" commit -q -m "Squashed" > /dev/null 2>&1 || true
git -C "$pr" push -q origin main
if bash "$here/cutover.sh" finish --out "$out" --yes > "$tmp/last.log" 2>&1; then fail "finish accepted a squash merge"; fi
grep -q "squashed or rebased" "$tmp/last.log" || fail "finish refused for the wrong reason: $(tail -1 "$tmp/last.log")"
ok "finish refuses a squash merge and explains why"

echo "Scenario 2b: a relative --new path (as in --new .) means the new checkout, not the working clone"
github=$tmp/github2b.git
fresh_github "$github"
out=$tmp/out2b
(cd "$new" && bash "$here/cutover.sh" prepare --old "$github" --new . --out "$out" > "$tmp/last.log" 2>&1) || { cat "$tmp/last.log" >&2; fail "prepare with --new . failed"; }
# shellcheck disable=SC1091,SC2154
. "$out/state.env"
[[ $new_sha == "$(git -C "$new" rev-parse development)" ]] || fail "--new . fetched the wrong commit"
[[ $(tree "$out/work" "$cutover") == "$(tree "$new" development)" ]] || fail "--new . did not produce the new tree"
ok "relative path resolved to the new checkout"

echo "Scenario 2c: pointing --new at the old repository itself is refused"
if bash "$here/cutover.sh" prepare --old "$github" --new "$old" --new-branch development --out "$tmp/out2c" > "$tmp/last.log" 2>&1; then fail "prepare accepted the old codebase as the new one"; fi
grep -q "already part of" "$tmp/last.log" || fail "refused for the wrong reason: $(tail -1 "$tmp/last.log")"
ok "refused"

echo "Scenario 3: prepare refuses uncommitted work in the new codebase"
echo dirty >> "$new/manage.py"
if bash "$here/cutover.sh" prepare --old "$tmp/github2.git" --new "$new" --out "$tmp/out3" > "$tmp/last.log" 2>&1; then fail "prepare accepted a dirty checkout"; fi
git -C "$new" checkout -q -- manage.py
ok "dirty checkout refused"

echo "Scenario 4: the secret scan flags a committed .env"
commit_file "$new" .env "SECRET=1" "oops"
if bash "$here/cutover.sh" scan --new "$new" > "$tmp/last.log" 2>&1; then fail "scan missed a committed .env"; fi
grep -q '\.env' "$tmp/last.log" || fail "scan did not name the file"
ok "scan flags it"

echo "All cutover self-tests passed."
