# Replacing the old repository's code with the new codebase

Goal: the existing GitHub repository (the old RosterChief) carries the new code on `main` and `development`, **both histories are kept**, nothing is force-pushed, and the old code can be brought back. The scripts are in `deploy/cutover/` and are rehearsed by `bash deploy/cutover/selftest.sh` (also run in CI).

## How it works

- The new codebase's tree arrives as a **merge commit** on top of the old `main`. First parent: the old `main`. Second parent: the new history. So every old commit and tag stays reachable.
- Before that, `legacy/main` and `legacy/development` branches and `legacy-final-main` / `legacy-final-development` tags are pushed at the old tips.
- A full **mirror** and a **bundle** of the old repository are written to the output directory before anything is pushed.
- The old CI workflow (`build-and-push.yml`, which builds and pushes ghcr.io images and auto-deploys a dev instance on pushes to `development`) is not in the new tree, so it disappears with the cutover. Images already in ghcr.io are not touched, because the new pipeline builds no images.

## Before you start

1. **Old production**: pin it to an immutable image tag (`IMAGE_TAG=v0.13.3` in its `.env`) instead of the floating `:main`, so nothing that happens to the repository can change what it pulls. Keep it running (frozen or read-only) until the new system is proven: rolling back *code* does not roll back *data* entered in the new system, because the import from the old one is one-way.
2. Make sure the new codebase's `development` branch is **committed** and the checkout is clean. Only committed work is published.
3. Use the **GitHub URL** as `--old`, not a local checkout: a local clone can be stale. The old checkout on this machine has its local `main` 129 commits behind `origin/main`, and the tag `v0.13.3` is only on `origin/main`.
4. The repository is **public**. Run the scan and read it: `deploy/cutover/cutover.sh scan --new . --new-branch development`. It is a heuristic; run a real scanner such as gitleaks too.
5. Note down GitHub settings the scripts cannot change: branch protection on `main`, and its **required status checks** (they name the old CI jobs and would block the PR forever).

## Steps

```bash
# 1. Back up and build locally. Pushes nothing. Writes ./repo-cutover-<time>/ (mirror, bundle, state.env, working clone).
deploy/cutover/cutover.sh prepare --old git@github.com:bsiebens/RosterChief.git --new . --new-branch development

# 2. Copy <out>/old-repo.bundle somewhere off this machine. Then publish the legacy refs and the cutover branch (never forced).
deploy/cutover/cutover.sh push --out <out>
```

3. On GitHub: protect `legacy/main` and `legacy/development` (no deletion, no force-push). Open a pull request `v2-cutover` → `main`. Check that the new `ci.yml` runs on it. Update the required checks, then merge with **Create a merge commit** (not squash or rebase: `finish` refuses those, because the new history would not be an ancestor).

```bash
# 4. Bring development across (a fast-forward, not forced), after the PR is merged.
deploy/cutover/cutover.sh finish --out <out>
```

5. Tidy by hand: close or delete stale Dependabot branches and PRs (they target the old files), remove the old deploy-dev secrets and variables from the repository's Actions settings, and add a new Dependabot config if you want one. Then cut the first release: `deploy/rc release 1.0.0`.

## Going back

| Situation | Command |
| --- | --- |
| Run the old code again (deploy, inspect) | Check out the tag `v0.13.3`, or `legacy/main` / `legacy/development`. The old images are untouched in ghcr.io. |
| Put the old files back on `main`/`development`, keeping history | `deploy/cutover/rollback.sh --out <out> --mode revert` (adds revert commits, nothing forced). To bring the new code back later, revert those revert commits. |
| Put `main`/`development` back at the exact old commits | `deploy/cutover/rollback.sh --out <out> --mode reset` (force-push with a lease, typed confirmation; needs branch protection to allow it for a moment). Commits pushed after the cutover are dropped from the branch. |
| Everything is wrong | Restore from `<out>/backup.git` (a full mirror) or `<out>/old-repo.bundle`. |

`--branches main|development|both` limits a rollback to one branch.

## What was tested

`selftest.sh` builds a fake GitHub (a bare repository), an old codebase with gitflow branches, a tag and its own workflow, and a new codebase with unrelated history, and checks:

- `prepare` pushes nothing and blocks accidental pushes from its working clone;
- `push` publishes the legacy refs and the cutover branch and leaves `main` alone;
- after the merge, `main` and `development` have exactly the new tree, the old workflow and files are gone, and the old branch tips, the old tag and the new history are all reachable;
- `finish` refuses a squash-merged pull request;
- `rollback --mode revert` restores the old files without a force-push, and `--mode reset` restores the exact old commits;
- a dirty new checkout is refused, and the scan flags a committed `.env`.

It was also rehearsed on a throwaway bare copy of the real old repository (922 commits on the local `main`, 64 tags) with this repository as the new codebase. That run found and fixed a bug the synthetic repositories missed: a relative `--new .` was resolved inside the working clone, so the "new" code silently became the old `development`. The final run showed `main` and `development` with exactly the new tree, the old `main` and `development` and the new history all reachable, the old workflow gone, and both rollback modes restoring the old files and the exact old commits. That copy was made from the local checkout, which is behind GitHub (see above), so it did not exercise the real `main`. Nothing has been pushed to the real GitHub repository.
