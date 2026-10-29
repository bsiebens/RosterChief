#!/usr/bin/env bash
# Cut a release or hotfix and leave its PR into main open on GitHub, ready for you to merge.
#
#   deploy/cut-release.sh release 0.7.14                   # ship development as 0.7.14
#   deploy/cut-release.sh hotfix 0.7.15 <commit>...        # ship main + these development commits
#   deploy/cut-release.sh --yes release 0.7.14             # skip the confirmation prompt
#   deploy/cut-release.sh --skip-audit hotfix 0.7.15 <commit>...   # don't run the dependency audit
#
# Runs the "Branching & release model" sequence from DEPLOYMENT.md, end to end except for the
# merge itself -- merging the PR stays your call, and so does deploying it afterwards
# (deploy/deploy.sh --master <host> --version vX.Y.Z once main's CI has tagged it).
#
# release: bumps pyproject.toml/uv.lock to the new version *on development* (so development
#   never drifts behind main's version again), pushes development, then branches
#   release/X.Y.Z from that tip and opens "Release X.Y.Z" against main.
#
# hotfix: branches hotfix/X.Y.Z from origin/main, cherry-picks the given commits onto it, bumps
#   the version there, and opens "Hotfix X.Y.Z" against main. The commits must already be on
#   development (fix it there first, then hotfix it out), so there is nothing to back-merge --
#   development gets the same version bump and is pushed as well. The version may equal main's
#   current one when that release never got tagged (e.g. a CI fix for a failed release build).
#
# Before anything is pushed or branched it audits the dependencies that would ship (pip-audit on
# uv.lock exactly like CI's `audit` job, honouring .github/pip-audit-ignore.txt; npm audit on the
# runtime JS). If either finds something it prints the findings and the commands to update the
# packages, then exits without touching anything -- fix on development, then run this again.
# --skip-audit is the escape hatch for when the advisory databases can't be reached or a hotfix
# can't wait.
#
# Refuses to run with uncommitted changes to tracked files, a version that's already tagged or
# not newer than the latest tag, or a release/hotfix branch that already exists.
set -Eeuo pipefail

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

ASSUME_YES=0
SKIP_AUDIT=0
while [[ "${1:-}" == -* ]]; do
    case "$1" in
        --yes|-y) ASSUME_YES=1 ;;
        --skip-audit) SKIP_AUDIT=1 ;;
        *) usage ;;
    esac
    shift
done
[[ $# -ge 2 ]] || usage

KIND="$1"; VERSION="$2"; shift 2
COMMITS=("$@")

case "$KIND" in
    release) [[ ${#COMMITS[@]} -eq 0 ]] || die "a release ships all of development; commits are only for hotfix" ;;
    hotfix)  [[ ${#COMMITS[@]} -gt 0 ]] || die "a hotfix needs at least one development commit to cherry-pick" ;;
    *) usage ;;
esac
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like X.Y.Z, got '$VERSION'"

BRANCH="$KIND/$VERSION"
TITLE="$(tr '[:lower:]' '[:upper:]' <<< "${KIND:0:1}")${KIND:1} $VERSION"
TAG="v$VERSION"

cd "$(git rev-parse --show-toplevel)"

# --- preflight ----------------------------------------------------------------
command -v gh >/dev/null || die "gh is not installed"
command -v uv >/dev/null || die "uv is not installed"
gh auth status >/dev/null 2>&1 || die "gh is not logged in (gh auth login)"
git diff --quiet && git diff --cached --quiet || die "you have uncommitted changes to tracked files"

step "Fetching origin"
git fetch --quiet --tags origin

git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "$TAG is already tagged"
git rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null && die "local branch $BRANCH already exists"
git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null && die "origin/$BRANCH already exists"

LATEST_TAG="$(git tag --list 'v*' --sort=-v:refname | head -1)"
MAIN_VERSION="$(git show origin/main:pyproject.toml | sed -n 's/^version = "\(.*\)"/\1/p')"
newest() { printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1; }
if [[ -n "$LATEST_TAG" ]]; then
    [[ "$VERSION" != "${LATEST_TAG#v}" && "$(newest "$VERSION" "${LATEST_TAG#v}")" == "$VERSION" ]] \
        || die "$VERSION is not newer than the latest release $LATEST_TAG"
fi
[[ "$(newest "$VERSION" "$MAIN_VERSION")" == "$VERSION" ]] || die "$VERSION is older than main's $MAIN_VERSION"

git checkout --quiet development
git merge --quiet --ff-only origin/development || die "local development has diverged from origin/development -- sort that out first"

for commit in ${COMMITS[@]+"${COMMITS[@]}"}; do
    git rev-parse -q --verify "$commit^{commit}" >/dev/null || die "unknown commit '$commit'"
    git merge-base --is-ancestor "$commit" development || die "$commit is not on development -- land the fix there first"
done

# --- dependency audit ---------------------------------------------------------
# Audits the lock files as they are at the ref that would ship (development for a release,
# origin/main for a hotfix), from a scratch copy, so the working tree is never touched.
audit_dependencies() {
    local ref="$1" dir report failed=0
    dir="$(mktemp -d)"
    for file in pyproject.toml uv.lock package.json package-lock.json; do
        git show "$ref:$file" > "$dir/$file" 2>/dev/null || { echo "  (no $file at $ref, skipping)"; rm -f "$dir/$file"; }
    done

    step "Auditing Python dependencies (pip-audit, uv.lock at $ref)"
    local ignore_args=() id
    while read -r id _; do
        case "$id" in ''|'#'*) continue ;; esac
        ignore_args+=(--ignore-vuln "$id")
    done < .github/pip-audit-ignore.txt
    # Same recipe as CI's audit job: production group only, git-sourced packages filtered out
    # (no advisory database covers them), the list is already fully resolved.
    local requirements="$dir/requirements.txt" py_status=0
    (cd "$dir" && uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt | grep -v ' @ git+' > "$requirements") \
        || { echo "error: could not export the Python dependencies at $ref" >&2; failed=1; }
    report="$dir/pip-audit.json"
    if [[ $failed -eq 0 ]]; then
        uvx pip-audit --disable-pip --no-deps --format json -r "$requirements" ${ignore_args[@]+"${ignore_args[@]}"} > "$report" 2> "$dir/pip-audit.err" || py_status=$?
        if [[ $py_status -ne 0 ]]; then
            failed=1
            if python3 - "$report" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)  # not a findings report: pip-audit itself failed
packages = [d for d in data.get("dependencies", []) if d.get("vulns")]
for dep in packages:
    for vuln in dep["vulns"]:
        fixes = ", ".join(vuln.get("fix_versions") or []) or "no fix released yet"
        print(f"  {dep['name']} {dep['version']}: {vuln['id']} (fixed in: {fixes})")
print()
print("  To update (then commit uv.lock on development, push, and re-run this script):")
for dep in packages:
    print(f"    uv lock --upgrade-package {dep['name']}")
if any(not v.get("fix_versions") for d in packages for v in d["vulns"]):
    print("  No fix is released for some of these: accept the advisory with a reason in")
    print("  .github/pip-audit-ignore.txt, or pass --skip-audit if this can't wait.")
PYEOF
            then :; else
                echo "error: pip-audit could not run (offline?):" >&2
                cat "$dir/pip-audit.err" >&2
            fi
        fi
    fi

    step "Auditing JavaScript dependencies (npm audit, runtime only, package-lock.json at $ref)"
    if [[ -f "$dir/package-lock.json" ]]; then
        if ! (cd "$dir" && npm audit --omit=dev --audit-level=high); then
            failed=1
            echo
            echo "  To update (then commit package-lock.json on development, push, and re-run this script):"
            echo "    npm audit fix --omit=dev          # or: npm update <package>"
            echo "    npm run vendor:htmx; npm run vendor:alpine; npm run vendor:chart   # if a vendored library changed"
        fi
    fi

    rm -rf "$dir"
    if [[ $failed -ne 0 ]]; then
        echo
        echo "error: the dependency audit failed -- nothing was changed. Fix the above, or use --skip-audit." >&2
        exit 1
    fi
}

if [[ $SKIP_AUDIT -eq 1 ]]; then
    echo "Skipping the dependency audit (--skip-audit)."
else
    command -v npm >/dev/null || die "npm is not installed (needed for the audit; or pass --skip-audit)"
    if [[ "$KIND" == release ]]; then audit_dependencies development; else audit_dependencies origin/main; fi
fi

# --- plan ---------------------------------------------------------------------
echo
echo "About to cut $TITLE (latest release: ${LATEST_TAG:-none}, main is at $MAIN_VERSION):"
if [[ "$KIND" == release ]]; then
    UNPUSHED="$(git rev-list --count origin/development..development)"
    echo "  - push development ($UNPUSHED unpushed commit(s)) with the version bump to $VERSION"
    echo "  - branch $BRANCH from development and open a PR into main shipping:"
    git log --oneline --no-merges --cherry-pick --right-only origin/main...development | sed 's/^/      /'
else
    echo "  - branch $BRANCH from origin/main and cherry-pick:"
    for commit in "${COMMITS[@]}"; do echo "      $(git log -1 --format='%h %s' "$commit")"; done
    echo "  - bump the version to $VERSION there and on development, push both, open a PR into main"
fi
echo
if [[ $ASSUME_YES -eq 0 ]]; then
    read -r -p "Proceed? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted, nothing changed."; exit 1; }
fi

trap 'echo; echo "error: stopped partway -- you are on $(git rev-parse --abbrev-ref HEAD); check git status before re-running." >&2' ERR

# Sets pyproject.toml + uv.lock to $VERSION and commits it, if they aren't there already.
bump_version() {
    uv version --quiet --no-sync "$VERSION"
    if ! git diff --quiet -- pyproject.toml uv.lock; then
        git add pyproject.toml uv.lock
        git commit --quiet -m "$1"
    fi
}

# --- development ----------------------------------------------------------------
step "Bumping development to $VERSION and pushing it"
bump_version "Bump version to $VERSION"
git push --quiet origin development

# --- release/hotfix branch -------------------------------------------------------
if [[ "$KIND" == release ]]; then
    step "Creating $BRANCH from development"
    git checkout --quiet -b "$BRANCH" development
else
    step "Creating $BRANCH from origin/main and cherry-picking"
    git checkout --quiet -b "$BRANCH" origin/main
    for commit in "${COMMITS[@]}"; do
        git cherry-pick -x "$commit" >/dev/null || die "cherry-pick of $commit conflicted -- resolve it, or 'git cherry-pick --abort' and delete $BRANCH"
    done
    bump_version "$TITLE"
fi
git push --quiet -u origin "$BRANCH"

# --- pull request ----------------------------------------------------------------
step "Opening the PR into main"
BODY="$(
    if [[ "$KIND" == release ]]; then
        echo "Release $VERSION: everything on \`development\` since ${LATEST_TAG:-the last release}."
    else
        echo "Hotfix $VERSION: cherry-picks the commits below from \`development\` onto \`main\`. They are already on \`development\`, so no back-merge is needed."
    fi
    echo
    echo "**Commits**"
    git log --format="- %h %s" --no-merges --cherry-pick --right-only "origin/main...$BRANCH"
    echo
    echo "Merge with a **merge commit** (not squash). CI then tags \`$TAG\` and cuts the GitHub Release; deploy with \`deploy/deploy.sh --master <host> --version $TAG\`."
)"
PR_URL="$(gh pr create --base main --head "$BRANCH" --title "$TITLE" --body "$BODY")"

git checkout --quiet development
trap - ERR

echo
echo "Done. $TITLE is open and waiting for you: $PR_URL"
echo "Once its checks are green and you're happy, merge it with a merge commit, then deploy:"
echo "  deploy/deploy.sh --master <host> --version $TAG"
