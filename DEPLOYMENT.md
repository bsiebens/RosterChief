# Deploying RosterChief

One server today, several later, with no code changes in between — only environment
variables. This document is the runbook and, more usefully, the list of things that are
specific to *this* app and will bite you if you treat it as a generic Django deploy.

## Branching & release model (git flow)

Two long-lived branches, both on GitHub, both protected (`deploy/configure-branch-protection.sh`):

| Branch | Role | Updated by | Builds/deploys |
|---|---|---|---|
| `development` | Integration — where feature branches land | `feature/*`/`fix/*` PRs, squash-merged | `.github/workflows/build-and-push.yml` pushes `:development`, then auto-deploys the throwaway dev/test instance (`deploy-dev` job) |
| `main` | Production | `release/*`/`hotfix/*` PRs, **merge commit** (not squash) | Same workflow also tags `vX.Y.Z` (read from `pyproject.toml`), pushes that tag, and cuts a GitHub Release. Deploying it to production stays a human's call — `deploy/deploy-prod.sh`, which already refuses anything but `main` |

**Day to day:**

```bash
git checkout development && git pull
git checkout -b feature/whatever-youre-building
# ... work, commit ...
git push -u origin feature/whatever-youre-building
gh pr create --base development                 # squash-merge once test is green
```

**Small, self-contained fixes may be pushed to `development` directly**, skipping the branch/PR
ceremony — `deploy/configure-branch-protection.sh` leaves `enforce_admins` off there (`main`
keeps it on: even the repo owner goes through a release/hotfix PR to reach production). Still
prefer a PR for anything that benefits from CI running *before* it lands rather than after —
this is a deliberate convenience for a solo maintainer, not a reason to skip judgment. Note
GitHub's `enforce_admins` is all-or-nothing: turning it off to skip the PR requirement also
lets an admin force-push or delete `development`, not just commit to it directly.

**Cutting a release** (branched from `development`, feature-frozen while `development` keeps
moving underneath it):

```bash
git checkout development && git pull
git checkout -b release/0.5.0
# bump pyproject.toml's version to 0.5.0, update anything release-specific, commit
git push -u origin release/0.5.0
gh pr create --base main --title "Release 0.5.0"       # merge with a real merge commit —
                                                          # this is the point that ships
```

**No back-merge into `development` after a release.** A release branch is never anything
`development` doesn't already have, plus the version bump — every change on it landed on
`development` first, via the ordinary `feature/*`/`fix/*` flow. There's nothing to bring back;
`development`'s own `pyproject.toml` just stays at whatever it last was until the *next*
release branch re-bumps it, which is cosmetic (nothing reads it except that footer) and costs
nothing. Back-merging anyway would only buy a second `test`+`build` CI run for a
one-line diff — pure waste, not safety.

The `main` merge triggers the workflow: it reads `0.5.0` back out of `pyproject.toml`, tags the
merge commit `v0.5.0`, pushes that tag, and opens a GitHub Release with auto-generated notes.
`deploy/deploy-prod.sh` should then be pointed at `IMAGE_TAG=v0.5.0` for that deploy — an
explicit, reviewed version number, not a floating branch tag (see "Deploying a specific
version" below).

**Hotfixing production** — branched from `main` instead of `development`, and **this one still
needs the back-merge**:

```bash
git checkout main && git pull
git checkout -b hotfix/0.5.1
# fix it, bump pyproject.toml to 0.5.1, commit, push
gh pr create --base main --title "Hotfix 0.5.1"              # ships immediately on merge
gh pr create --base development --title "Back-merge 0.5.1"    # the fix itself may not be on
                                                                # development yet -- see below
```

Unlike a release, a hotfix branch can carry a real code change `development` has never seen —
that's the whole reason it exists as its own category (urgent, can't wait for the normal
`feature/*` → `development` → release cycle). Skipping this back-merge risks the fix quietly
regressing the next time a release ships, since nothing about the ordinary flow would ever
reapply it. If a hotfix turns out to be nothing but a version bump and metadata (rare), the
back-merge PR will just be empty and harmless to skip by hand — but default to doing it.

**Why `development` was fast-forwarded to match `main` before any of this started:** it had
sat unmaintained (46 commits behind, zero commits of its own — the two branches shared a
common ancestor, not a real fork) while `main` served as the de facto trunk. Retrofitting git
flow onto that would have meant reconciling two diverged CI setups for no benefit; both
branches now start the new rules from the same point.

**GitHub can't enforce "squash into development, merge-commit into main" as a hard setting** —
the three merge-button methods (squash/merge/rebase) are a repo-wide toggle
(Settings → General → Pull Requests: leave squash and merge commits both enabled, rebase off,
so there's no third inconsistent option), not something scoped per target branch. The closest
real, enforceable lever is `development`'s own "require linear history" branch protection
rule, which GitHub *does* apply per-branch and which hard-blocks a merge-commit button from
even being offered there. `main` deliberately leaves that rule off, so a release/hotfix PR can
land as a real merge commit — see `deploy/configure-branch-protection.sh`.

**Required check on both branches: the `test` job** (lint + full suite) — nothing else is
required (see that script's own comment on why zero required approvals is the right default
for a solo maintainer, and where to change it once that stops being true).

## The five things that make this deployment unusual

**1. You need a wildcard TLS certificate, and that forces DNS-01.**
Tenancy is subdomain-based (`ajax.rosterchief.app`), so the certificate must cover
`*.rosterchief.app`. Let's Encrypt **will not issue a wildcard over HTTP-01** — only over
DNS-01, which means the TLS terminator needs API access to your DNS zone. That is why
`deploy/caddy/Dockerfile` builds Caddy *with* a DNS provider plugin, and why
`CLOUDFLARE_API_TOKEN` is a required variable rather than a nicety. Swap the plugin
(`caddy-dns/route53`, `caddy-dns/digitalocean`, …) if your DNS lives elsewhere.

DNS needs two records, both pointing at the server:

```
A   rosterchief.app    -> <server ip>
A   *.rosterchief.app  -> <server ip>
```

**2. Redis is not optional, even on one server.**
`waffle` caches each feature flag's targeting in the Django cache, and `LocMemCache` is
private to a single process. Under several gunicorn workers, toggling a feature in the
control panel flushes **one** worker's cache while the others keep serving the stale flag —
a feature that "sometimes doesn't turn on". A shared cache is the fix. It also carries the
in-process job scheduler's leader-election lease (see "Scheduled jobs" below) — harmless to
run on `LocMemCache` with one node (there's only ever one process trying to become leader:
the gunicorn master), but the thing that keeps exactly one node's scheduler active, not
every node's, the moment there's more than one `web` container running this image.

**3. `SECURE_PROXY_SSL_HEADER` must be set, and Caddy must send the header.**
Caddy terminates TLS, so without it Django believes every request is plain HTTP:
`request.is_secure()` goes false, WebAuthn disagrees with the browser about the origin, and
`SECURE_SSL_REDIRECT` becomes a redirect loop. Both halves are already wired (settings +
`header_up X-Forwarded-Proto`); don't remove either.

**4. Uploads must move to object storage before the second app server.**
Club logos go to `MEDIA_ROOT` on local disk by default. `compose.yaml` mounts a `media_data`
volume, shared read-write with `web` and read-only with `caddy`, so uploads both survive a
rebuild and get served by Caddy directly (`handle_path /media/*` in the Caddyfile) rather than
round-tripping through a gunicorn worker. `rosterchief/urls.py` still serves `/media/*` itself
as a fallback whenever `AWS_STORAGE_BUCKET_NAME` is unset — needed for `compose.behind-proxy.yaml`
(no bundled Caddy there) and for `runserver`. On two boxes local disk stops working regardless
of any of this: a logo uploaded to node A is still a 404 on node B, since nothing shares the
volume between them. Setting `AWS_STORAGE_BUCKET_NAME` switches the default storage to S3 — do
it *before* you scale, not during.

**5. PDF invoices need native libraries.**
WeasyPrint binds to pango/cairo. The image installs them; a bare-metal deploy would need
them too, and a Mac needs Homebrew. This is the main reason to run the container even in
development if you touch invoicing.

## First deploy

```bash
# 1. Clone and configure
git clone git@github.com:bsiebens/RosterChief.git /home/bernard/RosterChief
cd /home/bernard/RosterChief
cp .env.compose.example .env                 # read by docker compose
cp .env.production.example .env.production   # read by Django
python -c "import secrets; print(secrets.token_urlsafe(64))"   # -> DJANGO_SECRET_KEY
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
                                             # -> MFA_ENCRYPTION_KEY (see "Two-factor secrets")
openssl rand -hex 32                         # -> POSTGRES_PASSWORD, and again -> APP_DB_PASSWORD
                                             #    (the same value goes into DJANGO_DATABASE_URL;
                                             #    see "Database roles")

# 2. Pull and start
# The image is built on GitHub Actions (.github/workflows/build-and-push.yml), not here --
# see "Sizing the server" for why. `docker compose build` still works instead if you ever
# need to build locally.
docker compose pull
docker compose up -d db redis
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
docker compose up -d

# 3. Verify
curl -fsS https://rosterchief.app/healthz          # {"status": "ok", ...}
docker compose run --rm web python manage.py check --deploy
```

`check --deploy` is what catches an env file that forgot the HTTPS flags: they default to
**off** in code, because defaulting them to `not DEBUG` would redirect every test request to
https and break the suite anywhere `DEBUG` is unset.

### Deploying updates

Deploys run **from your own machine**; you never have to log into the server for them.
`deploy/deploy-prod.sh` opens one SSH session and does exactly what you would do by hand:
update the server's checkout, then run the server's own `deploy/deploy-local.sh` from it.

**One-time setup on your machine:**

```bash
cp deploy/.env.deploy.example deploy/.env.prod.local    # gitignored, never committed
# fill in SSH_HOST, SSH_USER, REMOTE_DIR (e.g. /home/bernard/RosterChief), optionally SSH_KEY_FILE
```

Check that `ssh <user>@<host>` works without a password prompt (key-based login), and that the
SSH user can run `docker` on the server.

**Every deploy:**

```bash
source deploy/.env.prod.local

deploy/deploy-prod.sh                      # the latest main (image :main)
deploy/deploy-prod.sh --version v0.7.8     # an exact release (image :v0.7.8) -- also how you roll back
deploy/deploy-prod.sh --push               # push your local main first, then deploy it
SKIP_BACKUP=1 deploy/deploy-prod.sh        # skip the pre-migration backup (not recommended)
```

What happens, in order:

1. **On your machine:** it refuses to deploy commits that aren't on GitHub yet (the server
   pulls from GitHub), and for `--version` checks the release tag exists.
2. **On the server:** `git fetch`, then `reset --hard` to `origin/main` (or checks out the
   release tag). Untracked files — `.env`, `.env.production`, backups — are never touched.
3. **On the server, `deploy/deploy-local.sh`:**
   - checks `.env.production` has every required key (`DJANGO_SECRET_KEY`,
     `MFA_ENCRYPTION_KEY`) **before** touching anything;
   - makes sure `db`, `redis` and `caddy` are up;
   - backs up the database (`deploy/backup.sh`, encrypted if `BACKUP_AGE_RECIPIENT` is set);
   - pulls the image, runs migrations, restarts `web`;
   - waits until `https://rosterchief.app/healthz` answers, and prints the recent `web` logs
     if it never does (the script then exits non-zero).

If anything fails, the script stops at that step and says why; `web` keeps running the old
version until the restart step.

**Rolling back:** `deploy/deploy-prod.sh --version v0.7.7` puts the previous release's code and
image back. Migrations are *not* reversed automatically: if the release you are leaving added a
migration the older code can't live with, restore the pre-deploy backup instead (see
"Backups → Restoring").

#### Deploying while logged into the server

`deploy/deploy-local.sh` is the same deploy without the SSH part — `deploy-prod.sh` runs this
very script. Update the checkout yourself first, then run it from the repo root:

```bash
git fetch origin && git checkout main && git reset --hard origin/main
deploy/deploy-local.sh                          # pulls and deploys :main
IMAGE_TAG=v0.7.8 deploy/deploy-local.sh         # an exact release (check out that tag first)
SKIP_BACKUP=1 deploy/deploy-local.sh            # not recommended
```

`deploy/deploy-dev.sh` (the test server) reads the same `SSH_HOST`/`SSH_USER`/`REMOTE_DIR`/
`SSH_KEY_FILE` variables — keep a separate `deploy/.env.dev.local` for it.

### Keep DJANGO_DEBUG=False, even on the test server

A test box is still a deployment: it is behind TLS, on a real domain, with real passkeys.
`DEBUG=True` there leaks tracebacks and settings to anyone who can reach a 500, and turns off
several of the protections in this document. Use it locally, not on a server.

The app no longer *crashes* if you set it — `django_browser_reload` is a dev dependency that
the image installs with `--no-dev`, so settings guard on the module being importable rather
than assuming DEBUG implies it is there — but the reason to keep it off is not the crash.

### One dependency comes from git

`django-lucide` is our fork (`[tool.uv.sources]` in `pyproject.toml`, pinned by `uv.lock` to a
commit), so **uv shells out to `git`** to fetch it. `python:*-slim` has no git, which is why
the image builds the virtualenv in a **separate stage** that installs git, and copies the
finished `.venv` into a runtime stage that does not have it — a build tool has no business in
a production image.

Two consequences worth knowing:

- The build needs **network access to GitHub**, and the fork must stay reachable. If that ever
  becomes awkward (a private runner, an air-gapped build), publish the fork to a private index
  or vendor the wheel, and the git stage disappears.
- `uv.lock` pins the exact commit, so the build is reproducible even though the source is a
  branch. Don't build with `--no-frozen`.

The first `docker compose up` will take a minute or two: Caddy is provisioning the wildcard
certificate over DNS-01, and DNS propagation is not instant. Watch it with
`docker compose logs -f caddy`.

## Migrations

Deliberately **not** run by the container's entrypoint. With more than one web container they
would race, and a starting gunicorn worker is a bad place to discover a failed migration.
Run them once, explicitly, as part of the deploy:

```bash
docker compose pull web
docker compose run --rm web python manage.py migrate
docker compose up -d --no-deps web
```

(`deploy/deploy-prod.sh` does exactly this, plus a backup first — see "Deploying updates".)

## Database roles

The app does not connect as the Postgres superuser. Two roles:

| Role | Set by | Used by |
|---|---|---|
| `POSTGRES_USER` (default `rosterchief`) — superuser | `.env` | the image's first-boot init, `deploy/backup.sh`, `deploy/restore-check.sh` (all via `docker compose exec db`, over the container's local socket) |
| `APP_DB_USER` (default `rosterchief_app`) — `NOSUPERUSER NOCREATEDB NOCREATEROLE` | `.env` (`APP_DB_USER`/`APP_DB_PASSWORD`) | `web` and every `manage.py` one-off, through `DJANGO_DATABASE_URL` in `.env.production` |

The app role **owns** the database and its `public` schema, so `migrate` can create and alter
tables; what it cannot do is anything outside that database — read other databases, create
roles, `COPY ... PROGRAM`, load extensions or files from the server. A SQL injection or a
leaked `DJANGO_DATABASE_URL` stays inside the app's own data.

### On a fresh volume

`deploy/postgres/10-app-role.sh` is mounted into `/docker-entrypoint-initdb.d`, so the first
`docker compose up -d db` on an empty `pgdata` volume creates the app role for you. Set
`APP_DB_USER`/`APP_DB_PASSWORD` in `.env` **before** that first start, and point
`DJANGO_DATABASE_URL` at the same user/password. The init script never runs again on an
existing volume — changing those variables later does nothing to the database.

### Converting an existing database

A database created before this existed is owned, table by table, by the superuser. Convert it
once (a few seconds; take a backup first). Generate a password without URL-special characters
so it can go into `DJANGO_DATABASE_URL` as-is:

```bash
cd /home/bernard/RosterChief
deploy/backup.sh /var/backups/rosterchief
APP_PW="$(openssl rand -hex 32)"; echo "$APP_PW"     # keep it for the two env files below

docker compose exec -T db psql -v ON_ERROR_STOP=1 -U rosterchief -d rosterchief \
  -v app_password="$APP_PW" <<'SQL'
BEGIN;
CREATE ROLE rosterchief_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE rosterchief OWNER TO rosterchief_app;
ALTER SCHEMA public OWNER TO rosterchief_app;
-- REASSIGN OWNED BY does not work here (the bootstrap superuser also owns system objects),
-- so hand over the app's own relations one by one: tables/views first, which takes their
-- identity/serial sequences along; then any sequence still left.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
             AND pg_get_userbyid(c.relowner) <> 'rosterchief_app'
  LOOP
    EXECUTE format(CASE r.relkind WHEN 'v' THEN 'ALTER VIEW public.%I OWNER TO rosterchief_app'
                                  WHEN 'm' THEN 'ALTER MATERIALIZED VIEW public.%I OWNER TO rosterchief_app'
                                  WHEN 'f' THEN 'ALTER FOREIGN TABLE public.%I OWNER TO rosterchief_app'
                                  ELSE 'ALTER TABLE public.%I OWNER TO rosterchief_app' END, r.relname);
  END LOOP;
  FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = 'S'
             AND pg_get_userbyid(c.relowner) <> 'rosterchief_app'
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO rosterchief_app', r.relname);
  END LOOP;
END $$;
REVOKE ALL ON DATABASE rosterchief FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE rosterchief TO rosterchief_app;
COMMIT;

-- Should print zero rows:
SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) <> 'rosterchief_app';
SQL
```

(Using a non-default `POSTGRES_USER`/`POSTGRES_DB`/`APP_DB_USER`? Substitute them for
`rosterchief`/`rosterchief`/`rosterchief_app` throughout.) Then switch the app over:

```bash
# .env            -> APP_DB_USER=rosterchief_app  APP_DB_PASSWORD=<APP_PW>
#                    (only documents it for a future fresh volume; the role already exists)
# .env.production -> DJANGO_DATABASE_URL=postgres://rosterchief_app:<APP_PW>@db:5432/rosterchief
docker compose up -d --no-deps --force-recreate web     # env_file changes need a recreate
docker compose run --rm web python manage.py migrate    # "No migrations to apply." -- as the app role
curl -fsS https://rosterchief.app/healthz
```

Nothing changes for backups: `deploy/backup.sh` and `deploy/restore-check.sh` keep running
`pg_dump`/`psql` as `POSTGRES_USER` inside the `db` container (local socket, no password). The
dump records the app role as owner, so restoring it needs that role to exist — it does on this
server; on a brand-new one, bring `db` up with the same `APP_DB_*` first. To roll back, point
`DJANGO_DATABASE_URL` at the superuser again; the superuser can still read everything.

## Two-factor secrets (`MFA_ENCRYPTION_KEY`)

TOTP secrets and recovery-code seeds are stored Fernet-encrypted (`authentication/crypto.py`),
under `MFA_ENCRYPTION_KEY` in `.env.production`. Passkeys hold no secret server-side and are
unaffected.

- **Required outside `DJANGO_DEBUG=True`.** Without it the `authentication.E001` system check
  fails `migrate` and `check` — which stops `deploy/deploy-prod.sh` before `web` restarts — and
  any TOTP sign-in would error. Local dev and the test suite derive a throwaway key from
  `DJANGO_SECRET_KEY` and need nothing.
- **Generate:** `python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`
  (or `Fernet.generate_key()` from `cryptography`).
- **Back it up separately from the database** (e.g. in the password manager next to the
  backup key below). Lose it and every TOTP user is locked out of their authenticator *and*
  their recovery codes; a staff account then needs its authenticators deleted in the admin
  and re-enrolled. It sits next to the ciphertext in no backup, which is the point.
- **Rotate:** put the new key first, the old one after it, comma-separated
  (`MFA_ENCRYPTION_KEY=new,old`). New writes use the first; both decrypt. Old values only get
  re-encrypted when a user re-enrols, so keep the old key in the list.

**Upgrading an existing deployment:** add `MFA_ENCRYPTION_KEY` to `.env.production` *before*
deploying the release that introduces it. Migration `authentication.0006_encrypt_mfa_secrets`
then encrypts the existing plaintext secrets in place (idempotent; reversible with
`migrate authentication 0005`). Values not yet encrypted are still read correctly, so there is
no window where sign-in breaks.

## Scheduled jobs

All eleven platform jobs run from **one in-process scheduler thread inside `web` itself** —
`features/scheduler.py`, an APScheduler `BackgroundScheduler` started by `gunicorn.conf.py`'s
`when_ready` hook. There is no host cron, no `worker`/`beat`, and no separate poller
container: the schedule is `features/jobs.py`'s `JOB_REGISTRY`, in code, in git, deployed with
every image — nothing left to configure by hand on a new box. Each job is a
`features.commands.ScheduledJobCommand` subclass — see `features/jobs.py` for what each one is
and `features/commands.py` for the shared Maintenance/JobToggle-aware, JobRun-recording base
class every one of them runs through.

| Job (management command) | Cadence | What it does |
|---|---|---|
| `extend_event_series` | daily 03:00 | materialises recurring event occurrences so the calendar never runs dry |
| `send_deadline_reminders` | daily 07:00 | nudges whoever hasn't answered an event, a week before its deadline (or start) |
| `publish_scheduled_lineups` | every 15 min | publishes any coach-scheduled line-up whose publish time has arrived |
| `poll_live_game_results` | every minute | refreshes score/live status from each game's competition data source, from 20 min before kickoff through 1h after its planned end (or until it's seen finishing) |
| `renew_subscriptions` | daily 04:00 | opens the next billing period for clubs whose current one is running out |
| `send_billing_reminders --commit` | daily 05:00 | emails club admins about outstanding platform fees, once per escalation level |
| `archive_overdue_clubs --commit` | daily 06:00 | archives clubs unpaid past their grace period |
| `generate_seasons` | monthly, 1st 05:00 | generates the next 2 years of seasons for every active club |
| `notify_published_news` | every 15 min | notifies the audience of any published news item whose publish time has arrived and hasn't been notified yet |
| `send_form_reminders` | daily 07:30 | nudges whoever hasn't submitted a form send yet, a few days before it closes |
| `publish_scheduled_announcements` | every 5 min | pushes any platform announcement whose scheduled time has arrived |

Cadences above are the human-readable label each job's `JOB_REGISTRY` entry carries (also
shown on the control panel's **Jobs** tab); the actual trigger the scheduler runs from is that
same entry's `trigger` field (an APScheduler cron/interval spec) — keep the two in sync by hand
if either ever changes, the same discipline the old crontab table needed, just in one file
instead of two.

**`--commit` is not optional for the two billing jobs it's shown on** — `send_billing_reminders`
and `archive_overdue_clubs` default to a dry-run/report-only preview (per their own `--help`);
without `--commit` the scheduler would tick them forever and nothing would actually happen. The
other seven act by default. `renew_subscriptions` also has a `--dry-run` to preview instead, for
manual use.

**Overlap and misfire protection is APScheduler's job config, not `flock`.** Every job is added
with `max_instances=1` (a still-running tick makes the next one skip, not queue — the same thing
`flock -n` gave the old crontab, per-job rather than one shared lock so a stuck
`notify_published_news` doesn't also block `publish_scheduled_lineups`) and
`coalesce=True, misfire_grace_time=300` (a tick or two missed — a deploy, a brief outage — runs
once to catch up instead of once per missed tick; older misfires are dropped rather than piling
up, the same "don't catch up the whole backlog" behaviour cron itself had).

**Exactly one process runs the schedule, enforced by a Redis lease, not by convention.**
`features.scheduler._try_acquire_or_renew` takes out a short-lived leader lease in the shared
cache before starting its local `BackgroundScheduler`, and renews it every 10 seconds for as
long as it holds it. Today, with one `web` container, this is nearly invisible — the gunicorn
master is the only process that ever tries. It's there for the day this scales to more than one
`web` node (see "Multi-server" below): every node runs the same scheduler code, but only the one
holding the lease actually ticks, the same "one scheduler" rule this document's multi-server
section already asked for by convention, now enforced automatically. A lost lease (a crashed
leader, Redis briefly unreachable) means at most ~30 seconds before another node picks it back
up — see that module's own docstring for the accepted small race window and why it isn't worth
a harder distributed lock at this job volume.

Run status (started, finished, success/failure, what it returned or raised) is recorded in
`features.models.JobRun` and shown on the control panel's **Jobs** tab — check there first if a
job seems to have gone quiet. The Jobs tab also has a **Run now** button per job, for testing one
off-schedule — it runs on a background thread (no request/gunicorn worker tied up waiting on it)
and goes through the exact same command, args, and JobRun bookkeeping a scheduled tick does,
`--commit` included where the registry has it.

Every job run also logs `job.start`/`job.finished`/`job.failed` lines (with elapsed time, and for
`job.start`, the OS pid) through Django's own `logging`, flushed immediately rather than sitting
in a stdio buffer — `docker compose -f compose.yaml logs web` is where to look first if a run
seems stuck: the last line reached tells you whether it got past creating its own `JobRun` row (a
DB-connectivity problem from the very first write) or hung somewhere inside the command's own
work. `scheduler.leader_acquired`/`scheduler.leader_lost` lines show which node currently owns
the schedule, if there's more than one.

Each command still has its own `--help` for manual/dry-run use from a shell (`generate_seasons
--resync`, for one, is still CLI-only: it can delete rows, so it isn't something a schedule
should ever run unattended, Run now button included).

### Why not host cron, and why not Celery

Both were tried. **Celery** (`worker`/`beat`) measured **~740 MB** for `worker` alone on a real
1 GB box — see "Sizing the server" for the full story (prefork forking defeats Python's
reference-counting-based copy-on-write once Django has ~46 apps loaded, so each forked child
pays close to the *full* import cost again). **Host cron** fixed the memory problem (a
`docker compose run` one-off pays the Django-import cost for a few seconds instead of 24/7,
and the once-a-minute `poll_live_game_results` ran as a single persistent process instead of
1,440 container starts a day) but traded it for an operational one: the schedule lived in
`/etc/cron.d/rosterchief` on the host, outside git, provisioned by hand on every new box, with
nothing to stop it drifting from `features/jobs.py`'s own idea of the schedule.

The in-process scheduler keeps Celery's actual lesson (no persistent worker *pool*, no
forking, no broker) while fixing what made cron itself annoying: the schedule is a Python
dict, reviewed in the same PR as the code it schedules, and shipped with the image instead of
configured separately per host. Expect it to cost a background thread's worth of memory inside
the process gunicorn's master already runs, not a new process — cheaper than even the old
`live_score_poller` container's ~50–100 MB, let alone Celery's `worker`.

## Maintenance mode

Control panel → **Features → Maintenance mode**. While it is on:

- every **club subdomain** serves a 503 maintenance page, in that club's own colours;
- the **control panel and the sign-in screens stay open**, because closing them would leave
  you with no way to turn it back off;
- `/healthz` keeps answering on every host, or the load balancer would take the node out of
  rotation and the control panel with it;
- the **scheduled jobs stand down** — the nine jobs in the table above, plus
  `import_members_csv` when run by hand.

`migrate` and `collectstatic` are deliberately **not** blocked. Maintenance is usually
declared *in order* to run them, and a guard that stopped them would mean turning the mode
off to do the work you turned it on for.

A scheduled job raises loudly rather than skipping quietly while the platform is closed —
that is intended, a job that silently no-ops is how a month of billing goes missing —
which `features.commands.ScheduledJobCommand` records as a `Failed` JobRun on the control
panel's **Jobs** tab (see that class's own docstring). Every command accepts
`--ignore-maintenance` for the rare case you genuinely mean to run one by hand during a
window.

So a migration-heavy deploy looks like:

```bash
# 1. Close the platform in the control panel (or from a shell):
docker compose run --rm web python manage.py shell -c \
  "from features.models import Maintenance; Maintenance.start(message='Upgrading. Back by 21:00.')"

# 2. Do the work — migrate is not blocked.
docker compose pull web
docker compose run --rm web python manage.py migrate
docker compose up -d --no-deps web

# 3. Reopen from the control panel.
```

The state lives in Redis as well as the database, so it takes effect on **every worker and
every server at once** — a per-process cache would leave some workers still serving clubs.

## Behind an existing Caddy (dev / test server)

If the box already runs Caddy on :80 and :443 — a test server sharing a host with other
sites — do **not** run ours: two Caddies cannot both hold port 80. Run the app only, publish
it on the loopback, and add a site block to the Caddy that is already there.

```bash
docker compose -f compose.behind-proxy.yaml up -d          # web + db + redis, no caddy
```

`web` publishes on `127.0.0.1:8001` (override with `WEB_PORT`). **Loopback, not 0.0.0.0** —
bound to all interfaces, a test instance is reachable at `http://<server-ip>:8001` with no
TLS, bypassing the proxy and every security header with it.

Then add a site block to the host's Caddyfile. Caddy serves any number of domains on the same
ports — TLS is chosen per connection by SNI — so a second (or tenth) site is just another
block.

### If that Caddy already does Cloudflare DNS-01

Which is the usual case: the box has a domain on Cloudflare and Caddy already has the DNS
plugin. Then set the challenge **once, globally**, and every site inherits it — no `tls`
block per site, and wildcards simply work:

```caddy
{
	email you@example.com

	# Applies DNS-01 to every site below.
	acme_dns cloudflare {env.CLOUDFLARE_API_TOKEN}

	# Behind Cloudflare's proxy: trust CF-Connecting-IP only from Cloudflare's own ranges,
	# so {client_ip} is the visitor. Copy the full, current list from deploy/caddy/Caddyfile
	# (source: https://www.cloudflare.com/ips/).
	servers {
		trusted_proxies static 173.245.48.0/20 103.21.244.0/22 ...
		client_ip_headers CF-Connecting-IP
	}
}

# --- whatever the box already serves --------------------------------------
existing-thing.example.com {
	reverse_proxy 127.0.0.1:3000
}

# --- RosterChief test instance --------------------------------------------
# The bare host AND the wildcard, on one certificate.
test.rosterchief.app, *.test.rosterchief.app {
	encode zstd gzip

	# Same per-request body cap as deploy/caddy/Caddyfile -- keep the two in step.
	request_body {
		max_size 64MB
	}

	reverse_proxy 127.0.0.1:8001 {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Real-IP {client_ip}
	}
}
```

### If the two domains need different tokens

Different Cloudflare accounts, or tokens scoped per zone. Drop `acme_dns` and give each site
its own `tls`; a snippet keeps it short:

```caddy
{
	email you@example.com
}

(cf) {
	tls {
		dns cloudflare {args[0]}
	}
}

existing-thing.example.com {
	import cf {env.CF_TOKEN_EXAMPLE}
	reverse_proxy 127.0.0.1:3000
}

test.rosterchief.app, *.test.rosterchief.app {
	import cf {env.CF_TOKEN_ROSTERCHIEF}
	request_body {
		max_size 64MB
	}
	reverse_proxy 127.0.0.1:8001 {
		header_up X-Forwarded-Proto {scheme}
	}
}
```

### What actually goes wrong

1. **The token must cover the *new* zone.** A Cloudflare token is scoped to named zones, and
   an existing one almost certainly grants `Zone:DNS:Edit` on the domain it was made for and
   nothing else. The new site then fails its DNS-01 challenge on a permissions error whose
   text does not say so. Widen the token, or mint a second one and use the snippet form.
2. **Both hostnames must be listed.** `*.test.rosterchief.app` does **not** match
   `test.rosterchief.app` — a wildcard covers exactly one label. Leave the bare host out and
   the club subdomains have a certificate while the control panel does not. Hence the comma.
   (Wildcards are also only one level deep: `ajax.test.…` yes, `a.b.test.…` no.)
3. **Caddy must have the DNS plugin.** Stock `caddy` cannot answer a DNS-01 challenge at all.
   `caddy add-package github.com/caddy-dns/cloudflare`, or run a Caddy built like
   `deploy/caddy/Dockerfile`. (If DNS-01 already works on the box, you have it.)
4. **The token must be in *Caddy's* environment**, not your shell's — `{env.…}` reads the
   process it runs in:

   ```ini
   # /etc/systemd/system/caddy.service.d/override.conf
   [Service]
   EnvironmentFile=/etc/caddy/caddy.env     # CLOUDFLARE_API_TOKEN=...
   ```

   Then `systemctl daemon-reload && systemctl restart caddy`.

5. **`header_up X-Forwarded-Proto` is not optional**, exactly as in the bundled Caddyfile:
   without it Django believes the request behind the proxy is plain HTTP. Keep the
   `request_body { max_size … }` cap too: it rejects oversized uploads (413) before they tie up
   a gunicorn worker. The bundled Caddyfile explains the value.

6. **Give the test instance its own subdomain tree** and set
   `ROSTERCHIEF_BASE_DOMAIN=test.rosterchief.app`. That variable drives tenant resolution,
   the shared session cookie *and* the WebAuthn RP ID — point it at the production domain and
   test passkeys start colliding with real ones.

### Applying and checking it

```bash
caddy validate --config /etc/caddy/Caddyfile   # syntax and modules
systemctl reload caddy                          # zero downtime; existing certs untouched
journalctl -u caddy -f                          # watch the DNS-01 challenge

curl -I https://test.rosterchief.app/healthz
curl -I https://any-club-slug.test.rosterchief.app/   # proves the WILDCARD, not just the host
```

Reloading provisions only what is new, so the existing site's certificate is not reissued.
Allow 30–60s for the DNS record to propagate before the challenge completes.

DNS needs both records, pointing at the test box:

```
A   test.rosterchief.app    -> <server ip>
A   *.test.rosterchief.app  -> <server ip>
```

The compose project is named `rosterchief-test`, so its containers and volumes never collide
with a production stack on the same host.

### Deploying with one command

Once the server has the repo cloned at `/home/bernard/RosterChief` and its two env files in
place, `deploy/deploy-dev.sh` does a full deploy over SSH:

```bash
deploy/deploy-dev.sh                    # deploy the current branch
BRANCH=development deploy/deploy-dev.sh # what CI itself deploys automatically -- see below
deploy/deploy-dev.sh --push             # push the branch first, then deploy
```

It runs from your machine and does the work on the server in one SSH session: fetch the pushed
branch (a hard reset to `origin/<branch>`, since a deploy target only receives deploys — this
is only for `compose.behind-proxy.yaml`/the script itself, not the app code, see below), pull
the image, run migrations *explicitly*, restart only `web`, and wait for `/healthz`.

It refuses to deploy a branch whose local commits are not pushed — the server pulls from git,
so unpushed work would ship stale code silently. Override the host, user, directory, branch, or
SSH identity with the `SSH_HOST` / `SSH_USER` / `REMOTE_DIR` / `BRANCH` / `SSH_KEY_FILE`
environment variables — see "Deploying updates" above for where to keep these set per target
rather than typing them every deploy.

#### Where the image comes from

The app image is **not** built on the server. `.github/workflows/build-and-push.yml` (workflow
`CI`) builds it on GitHub's own runners on every push to `development` or `main` and pushes it
to `ghcr.io/bsiebens/rosterchief`, tagged `:<branch>` and `:<branch>-<short-sha>` — plus, on
`main` only, `:vX.Y.Z` (see "Branching & release model" above). The server only ever
`docker compose pull`s — see "Sizing the server" below for why building on a small box is
what you're avoiding by doing this.

**Nothing reaches the registry untested.** The same workflow runs jobs in order:

1. `test` — `ruff check` + the full suite (`uv run python manage.py test`). Runs on every push
   *and* every pull request, against any branch.
2. `build` — only on a push to `development` or `main`, and only once `test` has passed
   (`needs: test`). Builds the image once locally (not pushed yet), boots it, and curls its own
   `/healthz` before building-and-pushing the real, published tags — catching a broken
   entrypoint or a missing static file that no unit test would ever see. On `main` specifically,
   it also tags/pushes the release's `vX.Y.Z` git tag and cuts a GitHub Release.
3. `deploy-dev` — opt-in (see below), redeploys the throwaway dev/test instance automatically
   once `build` succeeds **on `development`** — a `main` push (a release/hotfix landing) also
   triggers `build`, but this job explicitly checks the ref and does nothing for it, since
   deploying `development`'s image in response to a `main` release would be wrong, not just
   redundant.

A separate workflow, `.github/workflows/test-postgres.yml`, runs the identical suite against a
real `postgres:17` service container (compose.yaml's own major version) on a daily schedule
plus every push to `development`/`main` — the everyday `test` job above runs against the same
in-memory sqlite `rosterchief/test_runner.py` uses locally, which is fast but isn't what
production actually runs on.

**`test` is a required status check on both `development` and `main`** —
`deploy/configure-branch-protection.sh` sets this (and the rest of branch protection) from the
command line; see "Branching & release model" above for what it configures and why.

**Auto-deploying the dev instance.** The `deploy-dev` job runs `deploy/deploy-dev.sh` over SSH
the same way a person would, and is skipped entirely unless these repo secrets are set
(Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `DEV_SSH_HOST` | the dev server's host/IP (this repo's own dev box, matching "Behind an existing Caddy" below) |
| `DEV_SSH_USER` | the SSH user on that box |
| `DEV_SSH_KEY` | a private key authorized on that box, scoped to deploys only |
| `DEV_REMOTE_DIR` | the checkout path there, e.g. `/home/bernard/RosterChief` |

Worth tightening later: these currently live as plain repo secrets, readable by any workflow
run regardless of which branch triggered it. Moving them into a GitHub **Environment** named
`development` (Settings → Environments), and adding `environment: development` to the
`deploy-dev` job, scopes them so only a run deploying to that environment can read them —
free, and a real reduction in blast radius if a workflow file is ever compromised via a
malicious PR (repo secrets are available to `pull_request_target`/workflow-dispatch runs in a
way branch-restricted environment secrets are not). Not done here only because it's a UI step
this document can describe but not perform for you.

Production is deliberately **not** wired to auto-deploy — `deploy/deploy-prod.sh` stays a
human's call, with its own backup-before-migrate and refuse-non-`main` guardrails intact. If
that ever changes, gate it behind a `production` Environment with **required reviewers** —
the one GitHub feature that makes "auto-deploy to prod" mean "one click to approve", not "one
merge to prod."

**Deploying a specific version**: `deploy/deploy-dev.sh` always pulls `:$BRANCH` (latest for
that branch) — fine for the dev instance, which is meant to track `development`'s tip. For a
real production deploy, pin the release tag explicitly rather than a floating branch tag:

```bash
IMAGE_TAG=v0.5.0 docker compose pull web
IMAGE_TAG=v0.5.0 docker compose up -d --no-deps web
```

`:main-<short-sha>` still works for pinning an exact commit that hasn't been tagged as a
release (or for the dev/test box, `:development-<short-sha>`) — short SHAs come from the
GitHub Actions run, or `git log --oneline`. Setting `IMAGE_TAG` in the server's `.env` instead
makes it the new default for future plain `docker compose pull`s.

First-time setup on the server, once:

```bash
git clone git@github.com:bsiebens/RosterChief.git /home/bernard/RosterChief
cd /home/bernard/RosterChief
cp .env.compose.example .env             # fill in POSTGRES_PASSWORD etc.
cp .env.production.example .env.production
# then add the reverse_proxy site block to the host's Caddy (see above)
```

If the `ghcr.io/bsiebens/rosterchief` package is private (GitHub Packages defaults to matching
the repo's own visibility), the server also needs a one-time login before its first pull — a
GitHub personal access token with `read:packages` is enough, no push access needed:

```bash
echo "<token>" | docker login ghcr.io -u <your-github-username> --password-stdin
```

Making the package public instead (its own visibility setting under the repo's Packages tab
on GitHub) skips this entirely — reasonable here since the image contains no secrets, only
application code and dependencies (all secrets are `.env`/`.env.production`, never baked in).

## Backups

Everything that carries state is backed up by one script, `deploy/backup.sh`, run nightly by
cron on the production server:

| What | File |
|---|---|
| The database | `db-<date>.sql.gz.age` |
| Public uploads (logos, photos) — only while they're on local disk | `media-<date>.tar.gz.age` |
| Private uploads (onboarding documents, medical certificates) | `private-media-<date>.tar.gz.age` |

Each file is **encrypted** on the server with [age](https://age-encryption.org) before it is
stored or copied anywhere. The server only has the *public* key: it can encrypt, but nobody who
gets hold of the server or the backup location can read the backups. The *private* key, which
decrypts them, lives only with you.

The files are kept for 14 days on the server (`/var/backups/rosterchief`) and — the part that
actually protects you — copied every night to a **second server over SSH**, where they are kept
for 60 days. A backup on the same disk as the database survives a bad migration, not a dead
server.

Setting it up is a one-time job across three machines — your computer, the production server
and the backup server. Do the steps in order.

### Installing the tools

`age` (encryption) and `rclone` (the upload) are needed on the **production server**; `age` also
on **your own computer**. The backup server only needs a working SSH server.

**Debian / Ubuntu** (Debian 12+, Ubuntu 22.04+):

```bash
sudo apt install age rclone
```

**Amazon Linux 2023** (and Amazon Linux 2) — neither is packaged, so install the official
release binaries. Use `arm64` instead of `amd64` on Graviton (`t4g`, `m7g`, …) instances; check
with `uname -m` (`aarch64` = arm64, `x86_64` = amd64):

```bash
ARCH=amd64          # or arm64
AGE_VERSION=v1.3.2  # latest from https://github.com/FiloSottile/age/releases

curl -fsSL -o /tmp/age.tar.gz \
  "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${ARCH}.tar.gz"
tar -xzf /tmp/age.tar.gz -C /tmp
sudo install -m 0755 /tmp/age/age /tmp/age/age-keygen /usr/local/bin/
rm -rf /tmp/age /tmp/age.tar.gz

sudo dnf install -y "https://downloads.rclone.org/rclone-current-linux-${ARCH}.rpm"

age --version && rclone version | head -1      # both should print a version
```

Amazon Linux 2023 also ships **without cron**. Install and start it before step 5:

```bash
sudo dnf install -y cronie
sudo systemctl enable --now crond
```

**macOS** (your own computer): `brew install age` (and `brew install rclone` if you want it).

Binaries in `/usr/local/bin` (the Amazon Linux install above) are **not** on cron's default
`PATH` — the crontab in step 5 sets `PATH` for exactly that reason; keep that line.

### Step 1 — On your own computer: create the backup key

1. Install age (see "Installing the tools" above).
2. Create the key pair:

   ```bash
   age-keygen -o rosterchief-backup-key.txt
   ```

   It prints `Public key: age1…`. **Copy that line** — you need it in step 3. The file
   `rosterchief-backup-key.txt` is the private key.
3. Store the private key safely, **not on the production server**:
   - in your password manager (paste the whole file as a secure note), **and**
   - one offline copy (USB stick or printed, in a drawer/safe).

   Lose it and the backups can never be opened. Anyone who has it can read them. Store the
   production `MFA_ENCRYPTION_KEY` next to it — you need both to rebuild the server.

### Step 2 — On the backup server: an account that only receives files

The backup server can be any machine you can reach over SSH (a small VPS, a NAS, a box at
home). Log into it and create a dedicated account:

```bash
sudo useradd --create-home rosterchief-backup     # no password: key login only
sudo -u rosterchief-backup mkdir -p ~rosterchief-backup/.ssh ~rosterchief-backup/rosterchief
sudo chmod 700 ~rosterchief-backup/.ssh
```

(`useradd` exists on every distro, Amazon Linux included; the account gets no password, so it
can only be reached with the SSH key added below.)

Leave this session open; you'll paste a key into it in step 3.

### Step 3 — On the production server: age, rclone, SSH key, config

Log into the production server. The backup runs as **root** (it needs Docker), so run these
with `sudo -i` first.

1. **Install the tools:** `age` and `rclone` — see "Installing the tools" above (Amazon
   Linux needs the release binaries and `cronie`).

2. **Tell the backup which key to encrypt to.** Add the public key from step 1 to the compose
   `.env` in the checkout (the same file that holds `POSTGRES_PASSWORD`):

   ```bash
   cd /home/bernard/RosterChief
   echo 'BACKUP_AGE_RECIPIENT=age1…your public key…' >> .env
   ```

3. **Make an SSH key for the backup upload** (no passphrase: cron has to use it unattended):

   ```bash
   ssh-keygen -t ed25519 -N "" -C "rosterchief backups" -f /root/.ssh/rosterchief_backup
   cat /root/.ssh/rosterchief_backup.pub
   ```

4. **Allow that key on the backup server — for file transfer only.** In the step-2 session
   on the backup server, put the printed line into `~rosterchief-backup/.ssh/authorized_keys`,
   prefixed so the key can't open a shell:

   The `sftp-server` path differs per distro: `/usr/lib/openssh/sftp-server` on
   Debian/Ubuntu, `/usr/libexec/openssh/sftp-server` on Amazon Linux/RHEL/Fedora — check which
   exists with `ls` on the backup server and use that one:

   ```bash
   # on the backup server (Debian/Ubuntu path shown):
   echo 'restrict,command="/usr/lib/openssh/sftp-server" ssh-ed25519 AAAA…the key… rosterchief backups' \
     | sudo tee -a ~rosterchief-backup/.ssh/authorized_keys
   sudo chown -R rosterchief-backup: ~rosterchief-backup/.ssh
   sudo chmod 600 ~rosterchief-backup/.ssh/authorized_keys
   ```

5. **Trust the backup server's host key** (back on the production server, as root). Compare the
   fingerprint it shows with the one the backup server reports
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` there) before answering `yes`:

   ```bash
   ssh -i /root/.ssh/rosterchief_backup rosterchief-backup@<backup-host>
   # answer "yes" if the fingerprint matches; the login itself then closes (no shell -- that's intended)
   ```

6. **Create the rclone remote** called `offsite`:

   ```bash
   rclone config create offsite sftp \
     host <backup-host> user rosterchief-backup \
     key_file /root/.ssh/rosterchief_backup \
     known_hosts_file /root/.ssh/known_hosts \
     shell_type none
   rclone lsd offsite:          # should list the "rosterchief" folder
   ```

   (`shell_type none` because the key may only do file transfer; add `port <n>` if SSH on the
   backup server isn't on 22.)

### Step 4 — Test it once by hand

Still as root on the production server:

```bash
cd /home/bernard/RosterChief
BACKUP_REMOTE=offsite:rosterchief deploy/backup.sh /var/backups/rosterchief
ls -l /var/backups/rosterchief            # db-…sql.gz.age etc. -- only .age files are new
rclone ls offsite:rosterchief             # the same files on the backup server
```

The script prints `backup ok: …` and `copied to offsite:rosterchief`. If it stops with an
error, nothing half-written is left behind — fix what it says and run it again.

### Step 5 — Schedule it

`crontab -e` as root on the production server:

```cron
MAILTO=you@example.com
# cron's default PATH lacks /usr/local/bin (where age lives on Amazon Linux) -- keep this line.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Nightly at 02:30: dump, encrypt, keep 14 days here, copy offsite, keep 60 days there.
30 2 * * *  cd /home/bernard/RosterChief && BACKUP_REMOTE=offsite:rosterchief deploy/backup.sh /var/backups/rosterchief
```

`MAILTO` only works if the server can send mail (a local MTA such as `postfix`/`msmtp`); if it
can't, at least check `/var/backups/rosterchief` for fresh files after the first night, or
append `>> /var/log/rosterchief-backup.log 2>&1` to the line and check that log.

Knobs, all optional on that line: `KEEP_DAYS=14` (on the server), `REMOTE_KEEP_DAYS=60` (on the
backup server; `0` keeps everything forever). Cron mails `MAILTO` whenever the script fails,
and it fails loudly on purpose: a backup that fails quietly is worse than none.

Remove the old weekly `deploy/restore-check.sh` cron line if you have one: the server can no
longer decrypt the backups (by design), so the restore test moves to your own computer (next
step).

Every deploy also takes a backup first (`deploy/deploy-prod.sh` → `deploy-local.sh`); it's
encrypted the same way and stays on the server for 14 days, but isn't copied offsite — the
nightly run does that.

### Step 6 — Prove a backup restores (monthly, on your own computer)

A backup you've never restored is a hope, not a backup. On your own computer, with a checkout
of this repo and Docker:

**Once:** let your computer download from the backup server. Add your own public key
(`cat ~/.ssh/id_ed25519.pub`) to the backup account exactly like the server's key in step 3.4 —
same `restrict,command=…` prefix, appended with `tee -a` — so it can fetch files but not log in.

**Each time:**

```bash
# 1. fetch last night's database dump from the backup server
mkdir -p restore-test
sftp rosterchief-backup@<backup-host> <<'SFTP'
ls -lt rosterchief
SFTP
#    note the newest db-….sql.gz.age name, then:
sftp rosterchief-backup@<backup-host>:rosterchief/db-2026-07-14-0230.sql.gz.age restore-test/

# 2. start a throwaway local Postgres and restore into it
docker compose -f compose.behind-proxy.yaml up -d db
COMPOSE="docker compose -f compose.behind-proxy.yaml" \
  BACKUP_AGE_IDENTITY=~/rosterchief-backup-key.txt deploy/restore-check.sh ./restore-test
```

It prints `restore ok: … -> N clubs, M users`. Delete `restore-test/` afterwards — it holds
personal data.

### Restoring for real

The private key has to come to the machine you restore on — bring it for the restore and
remove it afterwards. On the production server, from the checkout:

```bash
# 1. stop the app so nothing writes during the restore
docker compose stop web

# 2. the database (as the Postgres superuser; the dump recreates everything)
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/db-2026-07-14-0230.sql.gz.age \
  | gunzip -c | docker compose exec -T db psql -U rosterchief rosterchief

# 3. uploads, if you lost them too
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/media-2026-07-14-0230.tar.gz.age \
  | docker compose exec -T web tar -xz -C /app
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/private-media-2026-07-14-0230.tar.gz.age \
  | docker compose exec -T web tar -xz -C /app

# 4. start again, and remove the key from the server
docker compose up -d web
shred -u rosterchief-backup-key.txt
```

If the server itself is gone, fetch the files from the backup server first
(`scp`/`rclone copy offsite:rosterchief ./`), set up a new server as in "First deploy", then
restore as above. The dump is taken with `--clean --if-exists`, so it replaces whatever is in
the database; restore as the superuser (`POSTGRES_USER`) — the application role (see
"Database roles") must already exist.

Once uploads move to S3 (`AWS_STORAGE_BUCKET_NAME`), the public-uploads archive is skipped: the
bucket's own versioning is their backup — turn versioning on when you create the bucket.
Private uploads always stay on local disk and are always in the backup.

### What lives where

| Item | Where |
|---|---|
| age **public** key (`BACKUP_AGE_RECIPIENT`) | production server, `.env` in the checkout |
| age **private** key | your password manager + one offline copy. Never on a server except during a restore |
| `MFA_ENCRYPTION_KEY` | production `.env.production` + next to the age private key |
| Upload SSH key (`/root/.ssh/rosterchief_backup`) | production server only; allowed on the backup server for file transfer only |
| Backups, 14 days | production server, `/var/backups/rosterchief` (root-only) |
| Backups, 60 days | backup server, `~rosterchief-backup/rosterchief` |

## Sizing the server

For **1–5 clubs, ~1000 members, ~10 events per club per week**.

The short answer: **2 vCPU, 4 GB RAM, 40 GB SSD** — a €4–6/month VPS (Hetzner CX22 or
equivalent). The interesting part is *why*, because the data is not what sizes this box.

### The data is negligible

Row counts for that workload, from the actual schema (attendance dominates: every event
invites a squad, so one event is ~20 rows):

| table | rows/year | MB/year |
|---|---:|---:|
| `events.Attendance` | 52,000 | 16 |
| `events.Event` | 2,600 | 2 |
| `formbuilder` answers | 10,000 | 3 |
| `shop` orders + lines | 3,000 | 1 |
| members, memberships, rosters | ~3,000 | 1 |
| **total, with WAL and bloat** | | **~40 MB/year** |

That is **0.2 GB after five years**. Uploads are club logos — a handful of files. Invoices are
rendered on demand and never stored. Nothing here grows into a problem.

So do not size for the data. Size for the **processes**.

### What actually consumes the box

Measured, running this app under gunicorn with `DEBUG=False`, before the tuning below —
`--workers 3`, no `--preload`, Postgres and Redis on their image defaults:

| | memory |
|---|---|
| gunicorn master + 3 workers | **~270 MB** (~54 MB per worker) |
| PostgreSQL (default `shared_buffers`) | ~200–400 MB |
| Redis (cache only) | < 50 MB |
| Caddy | ~30 MB |
| OS + Docker daemon | ~400 MB |
| **steady state** | **~1.0–1.2 GB** |

Since then, `Dockerfile`/`compose.yaml` were tuned for smaller boxes: `--workers 2 --preload`
(one fewer duplicated Django process, and `--preload` shares immutable memory across workers
via copy-on-write instead of each worker importing Django independently), plus trimmed Postgres
`shared_buffers`/`max_connections` and a Redis `--maxmemory` cap. Expect the gunicorn and
Postgres rows to come in lower than above — not yet re-measured, so treat the table as the
shape of where memory goes rather than exact numbers on the current config.

**There is no `worker`/`beat` row, and there used to be one** — worth the history, because it's
exactly the kind of thing that bites again if re-added carelessly. Scheduled jobs first ran as
Celery tasks on `worker`/`beat`, two more persistent Django processes. On a real 1 GB box,
`worker` alone measured **~740 MB** with the prefork pool's default `--concurrency=2`: prefork
forks child processes, and Python's own reference counting touches nearly every object's
refcount within the first few operations, defeating fork's copy-on-write sharing in practice —
each child pays close to the *full* Django-import cost again, not a fraction of it, and that
base cost is a lot bigger than gunicorn's own 54 MB now that there are 46 installed apps.
Switching `worker` to `--pool=threads` (no forking, so no multiplication, while still running
several tasks concurrently) closed most of that gap without losing the concurrency `--pool=solo`
gave up (a single slow task head-of-line-blocking everything queued behind it, including an
on-demand notification a member was actively waiting on). But the actual fix was realizing
neither process needed to be *persistent* at all: this app's job volume is a handful of daily/
monthly schedules plus occasional on-demand notifications, light enough that a plain
`docker compose run` one-off (host cron calling `manage.py <job>` directly — see "Scheduled
jobs") pays the Django-import cost for the few seconds a job actually runs instead of 24/7. Two
fewer persistent processes beats a smaller persistent process every time memory is the
constraint. `--pool=threads`/`--concurrency` never shipped; if a future change reintroduces a
real background-task queue, revisit this section's own math before assuming the old tuning still
applies — it was calibrated for a specific pool/concurrency combination, not the workload itself.

**Swapping shows up as both memory pressure and high sustained CPU** — the kernel spends cycles
on page faults and swap I/O instead of running the app, so the two symptoms are often the same
underlying problem, not separate ones. This is the practical reason the worker/beat measurement
above mattered: it wasn't just "less headroom," a process that size on a 1 GB box was enough to
push the whole host into swap, and everything else running there felt the CPU cost of that, not
just `worker` itself.

Even without `worker`/`beat`, **1 GB is tight** — the original table above (before either process
existed) already put steady state at ~1.0–1.2 GB for `web`/Postgres/Redis/Caddy/OS alone, before
a single cron job's brief spike lands on top. 2 GB is the real floor for this stack; 4 GB is the
recommendation for three further reasons, all of which are the kind of thing that bites at the
worst moment:

1. **`docker compose build` was the memory spike, not serving** — npm, uv and `collectstatic`
   together are enough to OOM a 2 GB box that's also running Postgres. This is why the image is
   built on GitHub Actions and the server only ever pulls it (see "Deploying with one command"
   above) rather than building in place; 4 GB is still the recommendation, since the other two
   reasons below don't go away.
2. **Rendering an invoice loads WeasyPrint.** It is imported lazily (which is why the workers
   measure 54 MB and not 150), so pango and its fonts land in whichever worker renders a PDF —
   expect that worker to grow by ~50–100 MB the first time someone downloads an invoice.
3. **Headroom is Postgres's page cache.** With 200 MB of data and 4 GB of RAM, the entire
   database lives in cache and the disk is never touched for reads.

### Disk

| | |
|---|---|
| Docker images (app ~1 GB with pango, postgres, redis, caddy) | ~1.5 GB |
| Build cache (only if you ever `docker compose build` locally on the box) | 2–4 GB |
| Database, 5 years | < 0.5 GB |
| Backups: 14 daily compressed dumps | < 0.5 GB |
| Logs | ~1 GB |
| **40 GB is roomy; 20 GB works** | |

### CPU and concurrency

2 vCPU. Three workers × four threads is twelve concurrent requests, against a peak of "the
whole club checks the Saturday line-up at 09:00" — perhaps a few hundred requests over a few
minutes. This workload is not CPU-bound; the one CPU-heavy operation is PDF rendering, which
happens a handful of times a month.

### When to grow

Not at "more members" — at these:

- **Uploads become real content** (photo galleries, documents). Media, not rows, is what makes
  storage grow, and it is also the trigger for moving to S3.
- **Attendance passes a few million rows** (~20 clubs at this rate, i.e. several years out).
  Add an index before adding a server.
- **You want zero-downtime deploys.** That is a second app node, not a bigger one.

## For fun: three nodes on AWS

Wildly over-engineered for 1000 members, but here is what it looks like — and what it costs.

### The layout

```
Route 53 (rosterchief.app + *.rosterchief.app)
        |
   ACM certificate (wildcard, free)
        |
Application Load Balancer  (TLS terminates here)
        |
   +----+----+----+
   |         |    |
 ECS task  task  task        3 × Fargate, one per AZ, same image
   |         |    |
   +----+----+----+
        |
   +----+---------------+----------------+
   |                    |                |
 RDS PostgreSQL   ElastiCache Redis    S3 (media)
 (Multi-AZ)       (cache.t4g.micro)    + CloudFront (optional)
```

**The one genuinely nice thing AWS gives you here: ACM issues the wildcard certificate for
free, with DNS validation in Route 53.** The whole DNS-01 dance disappears — no Caddy plugin,
no API token, no renewal. The ALB terminates TLS and forwards to the tasks. That is the single
biggest simplification versus the VPS.

### What changes in the app

Nothing in the code. Only environment:

| | |
|---|---|
| `DJANGO_DATABASE_URL` | the RDS endpoint |
| `DJANGO_REDIS_URL` | the ElastiCache endpoint |
| `AWS_STORAGE_BUCKET_NAME` | the media bucket — **required** now, three nodes cannot share a disk |
| `SECURE_PROXY_SSL_HEADER` | already set; the ALB sends `X-Forwarded-Proto` |
| health check | point the target group at **`/healthz`** — that is what it is for |

Sessions are database-backed, so **no sticky sessions**: any task can serve any request.

**Scheduled jobs get better here.** EventBridge Scheduler firing a one-off ECS task solves the
"run it on exactly one node" problem properly — no cron on three boxes racing each other:

```
EventBridge (cron: 0 6 * * ? *) -> ECS RunTask -> archive_overdue_clubs --commit
```

Backups become RDS automated snapshots + PITR, and `deploy/backup.sh` retires — though the
*restore rehearsal* does not. Snapshots you have never restored are still a hypothesis.

### Monthly cost (eu-central-1, on-demand, indicative)

| | | $/month |
|---|---|---:|
| ALB | fixed + a little LCU | ~22 |
| ECS Fargate | 3 × (0.5 vCPU, 1 GB) | ~54 |
| RDS PostgreSQL | `db.t4g.micro`, 20 GB gp3, single-AZ | ~17 |
| ElastiCache | `cache.t4g.micro` | ~12 |
| S3 + CloudFront | a few GB, low traffic | ~2 |
| Route 53 | hosted zone + queries | ~1 |
| ECR, CloudWatch logs | small | ~3 |
| | **single-AZ total** | **~110** |
| RDS Multi-AZ | doubles the database | +17 |
| | **highly-available total** | **~130** |

**Watch the NAT Gateway.** If the tasks sit in private subnets and reach the internet through
a NAT Gateway, add **~$32/month per AZ plus data charges** — for three AZs that is more than
the compute. Either put the tasks in public subnets with tight security groups, or use VPC
endpoints for ECR/S3/CloudWatch. It is the single most common surprise on an AWS bill of this
shape.

Prices are indicative and move; check the calculator before committing.

### The honest comparison

| | | |
|---|---|---|
| **Hetzner CX22** | 2 vCPU, 4 GB, 40 GB | **~€5/month** |
| **AWS, three nodes** | as above | **~$110–130/month** |

Roughly **25×**, for a workload whose database is 200 MB after five years. What the money buys
is real — managed Postgres with PITR, three AZs, no box to patch, free wildcard certificates —
but it is bought for *resilience*, not for capacity. At 1000 members you are paying for the
insurance, not the compute.

A reasonable middle: one VPS now, and move Postgres to a managed service (RDS, or a €15/month
managed Postgres) the day the data starts to matter more than the uptime. That is the change
that is painful to do late, and everything else in this document is already designed for it.

## Going multi-server

Nothing in the code changes. What changes is where the services live:

| | one server | several |
|---|---|---|
| Postgres | `db` container | `DJANGO_DATABASE_URL` → your central Postgres |
| Cache / flags | `redis` container | managed Redis (or your existing one) |
| Uploads | local disk | **S3 bucket** (`AWS_STORAGE_BUCKET_NAME`) |
| Static files | WhiteNoise, in the image | unchanged — that is why WhiteNoise is there |
| Scheduled jobs | in-process scheduler, the only node | unchanged — every node runs the same in-process scheduler, but `features/scheduler.py`'s Redis leader lease makes exactly one of them the active one automatically (see "Scheduled jobs" above); no separate infra needed, unlike the AWS layout below |
| TLS | Caddy on the box | load balancer, or Caddy on each node |

Drop `db` and `redis` from `compose.yaml`, point the URLs at the central services, and run
`web` on as many nodes as you like behind a load balancer pointed at `/healthz`.

The health check tests the database *and* a cache round trip, not just that the process is
listening — a node that cannot reach Postgres, or whose cache silently swallows writes, is
not healthy, and a load balancer must not keep feeding it traffic.

## Rollback

Images are the unit of rollback. For production, that's the release tag:
`.github/workflows/build-and-push.yml` tags every `main` build both `:vX.Y.Z` (the release —
see "Branching & release model") and `:main-<short-sha>` (immutable, for a commit that hasn't
been released), so rolling back is pointing `IMAGE_TAG` at the previous release:

```bash
IMAGE_TAG=v0.4.2 docker compose pull web
IMAGE_TAG=v0.4.2 docker compose up -d --no-deps web
```

Prior releases are listed under the repo's GitHub Releases page, or `git tag --list 'v*'`.

Setting `IMAGE_TAG` in the server's `.env` instead makes it the default for future plain
`docker compose pull`s — remember to unset it (or set it back to `main`) once you're done, or
the next `deploy-prod.sh` run will re-pull `:main` and quietly undo the pin anyway (`IMAGE_TAG`
is exported for the duration of that script regardless of what `.env` says).

Migrations are the exception: they don't roll back with the image. Prefer additive migrations
(add a column, deploy, backfill, then stop writing the old one) so that yesterday's image
still runs against today's schema. If a migration genuinely needs undoing, restore from the
backup `deploy-prod.sh` took immediately before it ran (see "Backups").
