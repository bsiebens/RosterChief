#!/usr/bin/env bash
# One-time (idempotent) setup of GitHub branch/tag protection for the git-flow model
# DEPLOYMENT.md's "Branching & release model" describes. Config, not a deploy -- run it once
# by hand, from a machine with `gh auth login`'d as an account with admin on the repo:
#
#   deploy/configure-branch-protection.sh
#
# Solo-maintainer defaults (see DEPLOYMENT.md for the reasoning): no required PR approvals --
# the `test` job's status check is the real gate everywhere that matters. `main` allows a real
# merge commit (release/hotfix branches merge in with one, on purpose, to keep those points
# visible in `git log`); `development` requires linear history, which is GitHub's only actual
# per-branch lever for "squash/rebase merges only" -- there is no separate "default merge
# method per branch" setting, so this is enforced here, not just by convention.
#
# `enforce_admins` differs per branch, on purpose: `main` keeps it on (even the repo owner goes
# through a release/hotfix PR to reach production); `development` turns it off, so a small,
# self-contained fix can be pushed directly rather than round-tripping through a PR that would
# only ever be self-merged anyway. GitHub's `enforce_admins` is all-or-nothing, though -- there
# is no "admins skip the PR requirement but still can't force-push" middle ground in classic
# branch protection, so turning it off for development also lets an admin force-push/delete it.
# Accepted here as low-risk on a solo-maintained branch; revisit (Rulesets' per-rule bypass
# actors support this distinction properly) if that stops being true.
#
# Re-run any time to reset drift back to these settings -- every `gh api` call below is a PUT
# (full replace) or an idempotent POST, not an incremental patch.
set -Eeuo pipefail

REPO="${REPO:-bsiebens/RosterChief}"
CHECK="${CHECK:-test}"  # build-and-push.yml's job id -- update here if that job is ever renamed

command -v gh >/dev/null || { echo "ERROR: gh CLI not found. https://cli.github.com/" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: run 'gh auth login' first, with an account that has admin on ${REPO}." >&2; exit 1; }

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

protect() {
    local branch="$1" linear_history="$2" enforce_admins="$3"
    say "Protecting ${branch} (required_linear_history=${linear_history}, enforce_admins=${enforce_admins})"
    gh api \
        --method PUT \
        -H "Accept: application/vnd.github+json" \
        "repos/${REPO}/branches/${branch}/protection" \
        -F "required_status_checks[strict]=true" \
        -f "required_status_checks[contexts][]=${CHECK}" \
        -F "enforce_admins=${enforce_admins}" \
        -F "required_pull_request_reviews[required_approving_review_count]=0" \
        -F "required_pull_request_reviews[dismiss_stale_reviews]=true" \
        -F "restrictions=null" \
        -F "required_linear_history=${linear_history}" \
        -F "allow_force_pushes=false" \
        -F "allow_deletions=false" \
        -F "required_conversation_resolution=true"
}

protect main false true            # release/hotfix -> main merges as a real merge commit; even
                                    # the repo owner goes through that PR, no bypass
protect development true false     # feature -> development merges squashed (or rebased); the
                                    # repo owner may also push small fixes directly

say "Protecting the v* tag pattern (release tags build-and-push.yml pushes)"
# The legacy `POST .../tags/protection` endpoint 404s on current GitHub -- tag protection now
# lives under the same Rulesets system as branch protection. Creation isn't blocked (the
# release step needs to create fresh v* tags); deletion and force-updating an existing one are.
existing_id=$(gh api "repos/${REPO}/rulesets?targets=tag" --jq '.[] | select(.name == "Protect release tags") | .id' 2>/dev/null || true)
if [ -n "$existing_id" ]; then
    echo "    (already exists, ruleset id ${existing_id} -- edit it directly if it needs to change)"
else
    gh api \
        --method POST \
        -H "Accept: application/vnd.github+json" \
        "repos/${REPO}/rulesets" \
        --input - <<'JSON'
{
  "name": "Protect release tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["refs/tags/v*"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" }
  ]
}
JSON
fi

cat <<EOF

Done -- verify what actually landed, this repo's exact settings may differ from what the API
silently coerces (e.g. required_approving_review_count: 0 is accepted but worth eyeballing):

  https://github.com/${REPO}/settings/branches
  https://github.com/${REPO}/settings/tag_protection

Once a second contributor joins, bump required_approving_review_count above and flip
development's enforce_admins back to true via the UI (or re-run this script after editing it)
-- both are solo-maintainer defaults, not permanent choices.
EOF
