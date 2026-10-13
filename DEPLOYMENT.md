# Deploying RosterChief

One server today, several later, with no code changes in between — only environment
variables. This document is the runbook and, more usefully, the list of things that are
specific to *this* app and will bite you if you treat it as a generic Django deploy.

## Branching & release model (git flow)

Two long-lived branches, both on GitHub, both protected (`deploy/configure-branch-protection.sh`):

| Branch | Role | Updated by | Builds/deploys |
|---|---|---|---|
| `development` | Integration — where feature branches land | `feature/*`/`fix/*` PRs, squash-merged | `.github/workflows/build-and-push.yml` pushes `:development`, then auto-deploys the throwaway dev/test instance (`deploy-dev` job) |
| `main` | Production | `release/*`/`hotfix/*` PRs, **merge commit** (not squash) | Same workflow also tags `vX.Y.Z` (read from `pyproject.toml`), pushes that tag, and cuts a GitHub Release. Deploying it to production stays a human's call — `deploy/deploy-prod.sh`, which only ever deploys `origin/main` or a `vX.Y.Z` tag |

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

**`deploy/cut-release.sh` automates both sequences below**, up to an open PR into `main` that
you then merge yourself:

```bash
deploy/cut-release.sh release 0.7.14                 # ship development as 0.7.14
deploy/cut-release.sh hotfix 0.7.15 <commit>...      # ship main + these development commits
```

It shows what it's about to ship and asks before changing anything (`--yes` skips the prompt).
It refuses a version that's already tagged or isn't newer than the latest release, and it won't
run with uncommitted changes. Before it changes anything it **audits the dependencies that would
ship** -- `pip-audit` on `uv.lock` (as CI's `audit` job does, honouring
`.github/pip-audit-ignore.txt`) and `npm audit` on the runtime JS. On a finding it prints the
advisories and the update commands (`uv lock --upgrade-package <name>`, `npm audit fix
--omit=dev`) and exits without touching anything: fix it on `development`, push, run it again.
`--skip-audit` bypasses this when the advisory databases are unreachable or a hotfix can't wait.
It differs from the manual sequences in two ways:

- **The version bump is committed on `development` too**, so `development` stays on the
  current version. A release branch is just `development`'s tip after that bump.
- **A hotfix cherry-picks commits that are already on `development`**: fix it there first, then
  hotfix it out. That makes the back-merge PR unnecessary.

Merging the PR and running `deploy/deploy-prod.sh --version vX.Y.Z` stay manual.

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
Deploy it with `deploy/deploy-prod.sh --version v0.5.0` — an explicit, reviewed version number,
not a floating branch tag (see "Deploying updates" below).

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

The bundled Caddyfile assumes both are **proxied** through Cloudflare (orange cloud): it trusts
`CF-Connecting-IP` only from Cloudflare's published ranges, which is how the visitor's real
address reaches Django (see 3). With proxying on, set Cloudflare's **SSL/TLS mode to
"Full (strict)"** — "Flexible" talks plain HTTP to the origin, Caddy redirects that to HTTPS,
and every page becomes a redirect loop. The API token needs **Zone → DNS → Edit** and
**Zone → Zone → Read** on the zone (Cloudflare's "Edit zone DNS" template gives exactly that).

**2. Redis is not optional, even on one server.**
`waffle` caches each feature flag's targeting in the Django cache, and `LocMemCache` is
private to a single process. Under several gunicorn workers, toggling a feature in the
control panel flushes **one** worker's cache while the others keep serving the stale flag —
a feature that "sometimes doesn't turn on". The same goes for the rate limits (allauth's
sign-in limits, `rosterchief.throttle` on the public registration and contact forms): they
count in the cache, so on `LocMemCache` every worker keeps its own count and the real limit is
several times the configured one. A shared cache is the fix (`DJANGO_REDIS_URL`). It also carries the
in-process job scheduler's leader-election lease (see "Scheduled jobs" below) — harmless to
run on `LocMemCache` with one node (there's only ever one process trying to become leader:
the gunicorn master), but the thing that keeps exactly one node's scheduler active, not
every node's, the moment there's more than one `web` container running this image.

**3. `SECURE_PROXY_SSL_HEADER` must be set, and Caddy must send the header.**
Caddy terminates TLS, so without it Django believes every request is plain HTTP:
`request.is_secure()` goes false, WebAuthn disagrees with the browser about the origin, and
`SECURE_SSL_REDIRECT` becomes a redirect loop. Both halves are already wired (settings +
`header_up X-Forwarded-Proto`); don't remove either. The same goes for the client address:
Caddy overwrites `X-Real-IP` with the visitor's IP (`{client_ip}`, resolved through
Cloudflare as above), and `ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP"` makes the sign-in
rate limits key on it. Any proxy in front of `web` **must** set that header — one that passes
a client-supplied `X-Real-IP` through lets anyone pick the address the rate limits see.

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

The commands below use `/home/deploy/RosterChief` as the checkout and `deploy` as the
login user — substitute your own. Everything runs as that user (in the `docker` group) unless
it says `sudo`.

### Preparing the server

Docker with the **compose v2 plugin** (`docker compose`, not the old `docker-compose`),
**buildx** (compose uses it to build the Caddy image) and git.

**Debian / Ubuntu** — Docker's own packages (the distro's `docker.io` often lacks the plugins):

```bash
curl -fsSL https://get.docker.com | sudo sh      # docker-ce + buildx + compose plugins
sudo apt install -y git
sudo usermod -aG docker "$USER"                  # log out and back in for it to take effect
docker compose version && docker buildx version
```

**Amazon Linux 2023** — `docker` is in the default repos, but neither the compose nor the
buildx plugin is, so install both as CLI plugin binaries. `uname -m` prints `x86_64` or
`aarch64` (Graviton); the compose download uses that name directly, buildx wants
`amd64`/`arm64`:

```bash
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"                  # log out and back in for it to take effect

sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)"

BUILDX_VERSION=v0.37.1                           # latest from https://github.com/docker/buildx/releases
BUILDX_ARCH=$([ "$(uname -m)" = aarch64 ] && echo arm64 || echo amd64)
sudo curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-buildx \
  "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-${BUILDX_ARCH}"

sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-buildx
docker compose version && docker buildx version  # both should print a version
```

(`/usr/local/lib/docker/cli-plugins` is one of the directories the docker CLI searches for
plugins on every distro; `/usr/libexec/docker/cli-plugins` works too.) Amazon Linux 2023 also
has no cron — install `cronie` when you get to "Backups".

The deploy user also needs to write the backups directory, because every deploy takes a
backup first (`deploy/deploy-local.sh` → `deploy/backup.sh /var/backups/rosterchief`) and the
nightly cron job runs as root. Create it once, owned by the deploy user; root can still write
into it:

```bash
sudo mkdir -p /var/backups/rosterchief
sudo chown "$USER": /var/backups/rosterchief && sudo chmod 700 /var/backups/rosterchief
```

### Cloudflare

Before the first start, so Caddy can get its certificate:

1. The two DNS records from "The five things…" above (`rosterchief.app` and
   `*.rosterchief.app`), **proxied**.
2. SSL/TLS → Overview → encryption mode **Full (strict)**.
3. My Profile → API Tokens → **Create token** from the "Edit zone DNS" template, scoped to the
   `rosterchief.app` zone. It goes into `CLOUDFLARE_API_TOKEN` below.

### Installing and starting

```bash
# 1. Clone. The repository is public, so HTTPS needs no key on the server (deploys `git fetch`
#    from this remote). If the repo ever goes private, clone over SSH with a read-only deploy key.
git clone https://github.com/bsiebens/RosterChief.git /home/deploy/RosterChief
cd /home/deploy/RosterChief

# 2. Configure. Two files, both gitignored:
cp .env.compose.example .env                 # read by docker compose (see the list below)
cp .env.production.example .env.production   # read by Django        (see the list below)
chmod 600 .env .env.production

python3 -c "import secrets; print(secrets.token_urlsafe(64))"                              # -> DJANGO_SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # -> MFA_ENCRYPTION_KEY
openssl rand -hex 32                         # -> POSTGRES_PASSWORD
openssl rand -hex 32                         # -> APP_DB_PASSWORD, and the same value in DJANGO_DATABASE_URL

# 3. Database and cache. The first start on an empty volume creates the app role from
#    APP_DB_USER/APP_DB_PASSWORD (see "Database roles"), so both must be in .env already.
docker compose pull web db redis             # caddy is built here, in step 4, not pulled
docker compose up -d db redis
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser

# 4. Everything else. The first `up` builds the Caddy image on the server (a Go build with the
#    Cloudflare DNS plugin -- a few minutes), then Caddy fetches the wildcard certificate.
docker compose up -d
docker compose logs -f caddy                 # wait for "certificate obtained successfully", then Ctrl-C

# 5. Verify
curl -fsS https://rosterchief.app/healthz          # {"status": "ok", ...}
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py sendtestemail you@example.com
```

`check --deploy` is what catches an env file that forgot the HTTPS flags: they default to
**off** in code, because defaulting them to `not DEBUG` would redirect every test request to
https and break the suite anywhere `DEBUG` is unset.

**Outgoing mail has to work before anyone can sign in.** Email verification is mandatory: an
account whose address isn't verified gets a confirmation link instead of a session — the
superuser you just created included. `sendtestemail` proves the SMTP settings; if mail isn't
working yet and you need in regardless, mark the superuser's address verified by hand:

```bash
docker compose exec web python manage.py shell -c "from allauth.account.models import EmailAddress; from authentication.models import User; u = User.objects.get(email='you@example.com'); EmailAddress.objects.update_or_create(user=u, email=u.email, defaults={'verified': True, 'primary': True})"
```

If the first `migrate` fails with `password authentication failed for user "rosterchief_app"`,
look at `docker compose logs db` for `10-app-role.sh: APP_DB_USER and APP_DB_PASSWORD must be
set`: `.env` was incomplete on the very first start, so the role was never created — and the
init scripts only ever run on an *empty* volume. Fix `.env`, then `docker compose down -v` (this
deletes the half-initialised database — only ever do it on a first install) and start again.
Otherwise the password in `DJANGO_DATABASE_URL` doesn't match `APP_DB_PASSWORD`.

The first start takes a minute or two after the Caddy build: Caddy is provisioning the
wildcard certificate over DNS-01, and DNS propagation is not instant. Until it has one,
Cloudflare answers with error 526 (invalid origin certificate) — that is Full (strict) doing
its job.

Then, before real users arrive: set up "Backups" (the nightly job, encryption and the offsite
copy), and the one-time setup on your own computer under "Deploying updates".

### Environment variables

**`.env`** — read by `docker compose` itself, for variable substitution in `compose.yaml` and
by `deploy/backup.sh`. Never seen by Django.

| Variable | Required | What |
|---|---|---|
| `ROSTERCHIEF_BASE_DOMAIN` | yes | for Caddy's site address — same value as in `.env.production` |
| `ACME_EMAIL` | yes | Let's Encrypt account email |
| `CLOUDFLARE_API_TOKEN` | yes | DNS-01 token, see "Cloudflare" above |
| `POSTGRES_DB` / `POSTGRES_USER` | no (`rosterchief` / `rosterchief`) | database name and the **superuser** |
| `POSTGRES_PASSWORD` | yes | superuser password |
| `APP_DB_USER` / `APP_DB_PASSWORD` | yes, before the first `db` start | the app's own role (default `rosterchief_app`) — see "Database roles" |
| `BACKUP_AGE_RECIPIENT` | yes, once backups leave the box | age public key backups are encrypted to — see "Backups" |
| `IMAGE_TAG` | no (`main`) | production: leave it at `main`, `deploy-prod.sh`/`deploy-local.sh` pass the tag they deploy (see "Rollback"). Test server: `development` (see "Deploying with one command") |
| `WEB_PORT` | no (`8001`) | `compose.behind-proxy.yaml` only |

**`.env.production`** — read by Django (python-decouple) inside `web`. `.env.production.example`
has all but the rarely-needed last row, with comments.

| Variable | Required | What |
|---|---|---|
| `DJANGO_SECRET_KEY` | yes | checked by `deploy/deploy-local.sh` before a deploy starts |
| `MFA_ENCRYPTION_KEY` | yes | see "Two-factor secrets"; also checked before a deploy starts |
| `DJANGO_DEBUG` | `False` | see "Keep DJANGO_DEBUG=False" below |
| `DJANGO_ALLOWED_HOSTS` | yes | `.rosterchief.app` (leading dot = every club subdomain) |
| `ROSTERCHIEF_BASE_DOMAIN` | yes | `rosterchief.app` — tenant resolution, the shared session cookie **and** the WebAuthn RP ID; changing it later invalidates every passkey |
| `ROSTERCHIEF_RP_NAME` | no (`RosterChief`) | name the browser shows in passkey prompts |
| `DJANGO_DATABASE_URL` | yes | `postgres://rosterchief_app:<APP_DB_PASSWORD>@db:5432/rosterchief` |
| `DJANGO_REDIS_URL` | yes | `redis://redis:6379/0` — see "The five things…", 2 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | no | `https://rosterchief.app` at most, **never** `https://*.rosterchief.app` (that would let any club subdomain post to any other). The CSRF token lives in the session (`CSRF_USE_SESSIONS`), so the old `DJANGO_CSRF_COOKIE_DOMAIN` is ignored — delete it if your file still has it |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE`, `DJANGO_SECURE_HSTS_*` | yes | the HTTPS flags — off in code, on in the example |
| `DJANGO_STATICFILES_BACKEND` | yes | `whitenoise.storage.CompressedManifestStaticFilesStorage`, matching what the image's `collectstatic` built |
| `DJANGO_EMAIL_*`, `DJANGO_DEFAULT_FROM_EMAIL` (or `RESEND_API_KEY`) | yes | SMTP (or Resend). Unset, mail is printed to the log and never sent — and nobody can verify an address, so nobody new can sign in |
| `ROSTERCHIEF_BILLING_CONTACT_EMAIL`, `ROSTERCHIEF_CONTACT_EMAIL` | no | reply-to for billing mail; where "Book a demo" requests go (defaults to the maintainer's address) |
| `DJANGO_VAPID_PUBLIC_KEY` / `DJANGO_VAPID_PRIVATE_KEY` / `DJANGO_VAPID_ADMIN_EMAIL` | no | Web Push for the mobile app; without a private key push is silently off. Generate a pair on your own machine with `uv run vapid --gen` |
| `AWS_STORAGE_BUCKET_NAME` and the other `AWS_*` | no | uploads to S3-compatible storage — see "The five things…", 4 |
| `APP_PUSH_APNS_*` | no | push for the native iOS app straight through Apple's APNs — see "Native app push". Without them the app works, just without pushes |
| `APP_MIN_VERSION_IOS` / `APP_MIN_VERSION_ANDROID` | no (`1.0.0`) | oldest app version still allowed; older apps show "update required" |
| `DJANGO_TIME_ZONE` | no (`Europe/Brussels`) | also the timezone of the job schedule |
| `DJANGO_SESSION_COOKIE_DOMAIN`, `DJANGO_SERVER_EMAIL`, `DJANGO_EMAIL_USE_SSL`, `DJANGO_EMAIL_TIMEOUT`, `DJANGO_INTERNAL_IPS`, `DJANGO_DB_CONN_MAX_AGE` (60), `DJANGO_PERFORMANCE_LOGGING*` | no | rarely needed; see `rosterchief/settings.py` |

### Deploying updates

Deploys run **from your own machine**; you never have to log into the server for them.
`deploy/deploy-prod.sh` opens one SSH session and does exactly what you would do by hand:
update the server's checkout, then run the server's own `deploy/deploy-local.sh` from it.

**One-time setup on your machine:**

```bash
cp deploy/.env.deploy.example deploy/.env.prod.local    # gitignored, never committed
# fill in SSH_HOST, SSH_USER, REMOTE_DIR (/home/deploy/RosterChief), optionally SSH_KEY_FILE
```

Check that `ssh <user>@<host>` works without a password prompt (key-based login), that the
SSH user can run `docker` on the server, and that it owns `/var/backups/rosterchief` (see
"Preparing the server"). `BASE_DOMAIN` (default `rosterchief.app`, for the health check) and
`COMPOSE_FILE` (default `compose.yaml`) can be exported in the same file if yours differ.

**Every deploy:**

```bash
source deploy/.env.prod.local

deploy/deploy-prod.sh --version v0.7.8     # an exact release (image :v0.7.8) -- the normal case, also how you roll back
deploy/deploy-prod.sh                      # the tip of main (image :main)
deploy/deploy-prod.sh --push               # push your local main first, then deploy the tip of main
SKIP_BACKUP=1 deploy/deploy-prod.sh ...    # skip the pre-migration backup (not recommended)
```

What happens, in order:

1. **On your machine:** `git fetch --tags`. With `--version`, it checks the tag looks like
   `vX.Y.Z` and exists (CI creates it when a release PR merges into `main`); without, it refuses
   if your local `main` has commits GitHub doesn't (the server pulls from GitHub).
2. **On the server:** `git fetch`, then `checkout main` + `reset --hard origin/main` — or, with
   `--version`, a detached checkout of the tag. Untracked files — `.env`, `.env.production` —
   are never touched.
3. **On the server, `deploy/deploy-local.sh`** from that fresh checkout:
   - checks `.env.production` has non-empty `DJANGO_SECRET_KEY` and `MFA_ENCRYPTION_KEY`
     (and that `.env` exists) **before** any container is touched; notes it if
     `BACKUP_AGE_RECIPIENT` is missing;
   - `up -d --remove-orphans db redis`, then starts `caddy` without touching `web` — and
     recreates `caddy` when `deploy/caddy/Caddyfile` changed (the running copy is compared
     with the checkout), so Caddyfile changes apply on deploy;
   - backs up (`deploy/backup.sh` into `BACKUP_DIR`, default `/var/backups/rosterchief` —
     database and uploads, encrypted when `BACKUP_AGE_RECIPIENT` is set). Skipped, with a
     note, when `web` isn't running (a first deploy: nothing to back up yet);
   - pulls the image, runs `migrate --noinput` in a one-off container, recreates `web`;
   - polls `https://rosterchief.app/healthz` for up to a minute, and prints the last 40 `web`
     log lines (and exits non-zero) if it never answers.

If anything fails, the script stops at that step and says why; `web` keeps running the old
image until the restart step. A failed *migration* has already been preceded by the backup —
see "Rollback" for when you need it.

**Caddy config changes apply automatically** (a second or two of downtime while `caddy` is
recreated; certificates are kept in the `caddy_data` volume). Only a change to
`deploy/caddy/Dockerfile` (the Caddy build itself, rare) needs a manual rebuild after the deploy:

```bash
docker compose build caddy && docker compose up -d --force-recreate caddy
```

#### Deploying while logged into the server

`deploy/deploy-local.sh` is the same deploy without the SSH part — `deploy-prod.sh` runs this
very script. Update the checkout yourself first, then run it from the repo root:

```bash
cd /home/deploy/RosterChief
git fetch --tags origin && git checkout main && git reset --hard origin/main
deploy/deploy-local.sh                          # pulls and deploys :main

git fetch --tags origin && git checkout --detach v0.7.8
IMAGE_TAG=v0.7.8 deploy/deploy-local.sh         # an exact release: tag checkout and image tag must match

SKIP_BACKUP=1 deploy/deploy-local.sh            # not recommended
```

`deploy/deploy-dev.sh` (the test server, see "Behind an existing Caddy") reads the same
`SSH_HOST`/`SSH_USER`/`REMOTE_DIR`/`SSH_KEY_FILE` variables — keep a separate
`deploy/.env.dev.local` for it.

### Upgrade notes: 0.7.8

One-off steps for the release that carries the security work (MFA encryption, mandatory email
verification, session-stored CSRF tokens, encrypted backups, the app database role). Do them in
this order; delete this list once 0.7.8 is live everywhere.

**Before pushing it to `development`** (CI auto-deploys the test server — without the key its
`migrate` fails on `authentication.E001` and the old version keeps running):

1. On the test server, add `MFA_ENCRYPTION_KEY` to `.env.production` (its own key, not
   production's — see "Two-factor secrets"), set `DJANGO_CSRF_TRUSTED_ORIGINS=https://test.rosterchief.app`
   and delete any `DJANGO_CSRF_COOKIE_DOMAIN` line.
2. Update the host Caddy's site block for the test instance to the current snippet in "Behind an
   existing Caddy": the `request_body { max_size 64MB }` cap and `header_up X-Real-IP {client_ip}`
   (plus the `trusted_proxies` global block if its records are proxied through Cloudflare).
   `caddy validate`, `systemctl reload caddy`.

**Before deploying to production:**

3. `.env.production`: add `MFA_ENCRYPTION_KEY` (generate as in "First deploy"), and store a copy
   next to the backup key in your password manager. Migration
   `authentication.0006_encrypt_mfa_secrets` encrypts the existing TOTP secrets with it.
4. `.env.production`: `DJANGO_CSRF_TRUSTED_ORIGINS=https://rosterchief.app` — drop the
   `https://*.rosterchief.app` entry — and delete `DJANGO_CSRF_COOKIE_DOMAIN` if present (now
   ignored). Everyone gets a fresh CSRF token on their next page load; a form left open across
   the deploy may need one resubmit.
5. **Email must work.** Verification is now mandatory for signing in. Migration
   `authentication.0005_verify_existing_account_emails` marks every existing account's current
   address as verified, so nobody is locked out by the deploy itself — but new sign-ups and
   email changes need the confirmation mail. Check the SMTP/Resend settings in `.env.production`
   (`sendtestemail`, see "First deploy") and that **"pause automated email"** is off in the
   control panel: it also holds back allauth's confirmation mails.
6. Backups: install `age`, put `BACKUP_AGE_RECIPIENT` in `.env`, make `/var/backups/rosterchief`
   writable by the deploy user and (re)do the cron line — all in "Backups". Remove an old
   `deploy/restore-check.sh` cron line if you have one. `deploy-local.sh` only *warns* when the
   recipient is missing, and then the pre-deploy backup is plaintext.

**Deploy:**

7. `deploy/deploy-prod.sh --version v0.7.8` (see "Deploying updates").
8. Check the deploy output says **"Caddyfile changed -- recreating caddy"**: this release
   changes it (Cloudflare `trusted_proxies`, `X-Real-IP {client_ip}`, the 64 MB body cap,
   headers on `/media/`). The deploy that brings in the new `deploy-local.sh` is the first one
   that does this — so for *this* release only, run it once more by hand to be sure:

   ```bash
   cd /home/deploy/RosterChief
   docker compose up -d --force-recreate caddy
   ```

**After deploying** (on the production server, from the checkout; do 9 and 10 on the test server
too, with `docker compose -f compose.behind-proxy.yaml`):

9. Delete the invoice PDFs earlier releases cached in public storage — list first, then delete:

   ```bash
   docker compose exec web python manage.py purge_public_invoice_cache --dry-run
   docker compose exec web python manage.py purge_public_invoice_cache
   ```

10. Nothing to do for Web Push: subscriptions stored before this release whose endpoint isn't
    one of the browser vendors' push services are never sent to, and are deleted the first
    time a notification would go to them. To see how many there are (optional):

    ```bash
    docker compose exec web python manage.py shell -c "from mobile.models import PushSubscription; from mobile.services.push import is_allowed_push_endpoint; print(sum(not is_allowed_push_endpoint(s.endpoint) for s in PushSubscription.objects.all()))"
    ```

11. Sign in with a TOTP account (proves the key and the encryption migration), and check
    `docker compose logs --tail 100 web` for errors.
12. Optional, whenever convenient: move the app off the Postgres superuser — "Database roles →
    Converting an existing database". Try it on the test server first.

**Rolling back from 0.7.8** needs one extra step *first*: v0.7.7 can't read encrypted TOTP
secrets, so decrypt them while 0.7.8 is still running, then deploy the old release:

```bash
docker compose exec web python manage.py migrate authentication 0005   # on the server
deploy/deploy-prod.sh --version v0.7.7                                  # from your machine
```

### Upgrade notes: 0.7.9

One-off steps for the release after 0.7.8. If 0.7.8's steps above aren't done yet on a server,
do those first. Delete this list once 0.7.9 is live everywhere.

**Before pushing to `development`** (the test server's auto-deploy — its CI job fails until
these are done):

- **Pin the test server's SSH host key** for CI (the deploy job no longer trusts it on first
  use). From a machine you trust, with the exact host name stored in the `DEV_SSH_HOST` secret:

  ```bash
  ssh-keyscan -t ed25519 <DEV_SSH_HOST> > dev_known_hosts
  ssh-keygen -lf dev_known_hosts                # fingerprint as seen over the network
  ```

  On the test server itself (console or an already-trusted session), check it matches:
  `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Then store it:
  `gh secret set DEV_SSH_KNOWN_HOSTS --repo bsiebens/RosterChief < dev_known_hosts`.
  (Non-standard SSH port: `ssh-keyscan -p <port> …`.) The job now runs in a GitHub
  Environment called `dev`; you can add protection rules to it under Settings → Environments.
- **Strong `DJANGO_SECRET_KEY` on both servers.** `migrate`/`check` now refuse (system check
  `rosterchief.E001`) a key shorter than 50 characters, one with few distinct characters, or one
  containing placeholder words (`change`, `example`, `insecure`, …). If yours fails, generate a
  new one (`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`) — changing it signs
  everyone out once.

**Before deploying to production:**

- **Shop licence flag.** The member shop now also needs the club's `shop` feature flag (the
  management side already did). Check on the control panel's Features page that every club with
  its shop open has `shop` switched on — otherwise its members' shop disappears.
- **Caddy is rebuilt from pinned versions** (`deploy/caddy/Dockerfile` now pins Caddy 2.11.4 and
  the Cloudflare plugin v0.2.4 by digest). The deploy recreates `caddy` when the Caddyfile
  changes but doesn't rebuild the image, so after the deploy run once:
  `docker compose build caddy && docker compose up -d --force-recreate caddy`.

**What changes at runtime** (no action, but know it):

- **Containers are locked down:** `web` runs with a read-only filesystem (only the two media
  volumes and an in-memory `/tmp` are writable), no Linux capabilities and no privilege
  escalation; `caddy` keeps only the right to bind ports 80/443; `db` and `redis` keep only what
  their start-up needs. Anything a command writes outside the volumes or `/tmp` now fails with
  "Read-only file system" — intended. In a debug shell, write scratch files to `/tmp` (256 MB,
  gone when the container stops).
- **Base images are pinned by digest** (Python, Node, uv, Caddy, Postgres 17.11, Redis 7.4.11).
  Minor updates no longer arrive by themselves on `docker compose pull`; Dependabot opens PRs for
  them weekly (also for GitHub Actions, Python and npm packages).
- **`/admin/` only answers on `rosterchief.app`**, never on a club's subdomain (404 there, in
  both Caddy and Django).
- **Calendar sync links:** existing subscriptions keep working, but the link is no longer shown
  again — members who lost theirs use "Get a new calendar link" (which stops the old one).
- **Form file uploads work** (they failed before) and are stored privately under
  `private_media/formbuilder/`, so they're in the nightly `private-media-…` backup.
- **Deploys stop on configuration errors:** after migrating, `deploy-local.sh` runs
  `check --deploy --fail-level ERROR` before restarting `web`.
- **Dependency audit in CI:** a new `audit` job checks Python and npm packages for known
  vulnerabilities on every push; accepted advisories go in `.github/pip-audit-ignore.txt`.


**After deploying** (on the production server, from the checkout; do 1 on the test server too,
with `docker compose -f compose.behind-proxy.yaml`):

1. **Member photos are private now.** Migration `members.0012_move_member_photos_to_private_storage`
   has moved every member's current photo from public storage into private storage (the
   `private_media` volume) and deleted the public copy — on S3 setups that means from the
   bucket onto the volume. Photos a member replaced earlier aren't referenced by any row, so the
   migration can't know them; delete those leftovers — list first, then delete:

   ```bash
   docker compose exec web python manage.py purge_public_member_photos --dry-run
   docker compose exec web python manage.py purge_public_member_photos
   ```

   It only touches `members/` in public storage. A photo the migration couldn't find in either
   place is logged (`Member photo … not found in either storage`) and its row left as is — check
   the migrate output in the deploy log. From now on photos are in the nightly
   `private-media-…` backup, so that archive grows by their size. With S3 versioning on, the
   bucket keeps noncurrent versions of the deleted photos until its lifecycle rule expires
   them; they aren't reachable through the public URLs.

2. **Shop discounts: nothing to run.** Migration `shop.0031_backfill_applied_discount_amount`
   fills in the euro amount each applied discount took off its order. Existing order totals are
   deliberately left as they are, even where the new rule (a percentage stays a percentage when
   lines change) would give a slightly different figure; when staff re-save a line of such an
   order, its discounts and total are recomputed and agree again.

3. **Backups become append-only** (`deploy/backup.sh` and the backup server, see "Backups" for
   the why and every command). Do this *after* the deploy, in one sitting: once the key line
   carries the `-P` list, the old `backup.sh` (renames, remote pruning) can't upload any more,
   and the new one expects the `incoming` folder.

   a. **Backup server** (your admin login): create `incoming`, make `.ssh` and
      `authorized_keys` root-owned, create the archive directory and install the archive job —
      "Backups", step 2. Replace the upload key's line in `authorized_keys` with the one with
      `-P …` from step 3.4. If you added your own computer's key to the account for restores
      (the old step 6), remove it: restores now use your admin login.

   b. Move the backups already there into the archive, once (they keep their dates, so pruning
      carries on where it was):

      ```bash
      sudo sh -c 'mv ~rosterchief-backup/rosterchief/*.age /var/lib/rosterchief-backups/archive/ && chown root:root /var/lib/rosterchief-backups/archive/*'
      ```

   c. `sudo crontab -e` on the backup server: the archive line from step 5.

   d. **Production server**, as root: `rclone config update offsite set_modtime false`, and in
      root's crontab change `BACKUP_REMOTE=offsite:rosterchief` to
      `BACKUP_REMOTE=offsite:rosterchief/incoming` and drop any `REMOTE_KEEP_DAYS=…` (retention
      is `ARCHIVE_KEEP_DAYS` on the backup server now).

   e. Test as in step 4 (the `sftp` check from step 3.5 too: `rmdir` must be refused). The first
      run has no `.offsite-sent` yet, so it uploads every `.age` file still on the server — the
      archive job drops the ones it already has (identical re-uploads) and archives the rest.

### Upgrade notes: 0.7.10

No server-side steps: 0.7.10 changes only CI and tests (Node 24 action versions, the suite
sharded over four jobs, a Postgres-only test fix). Deploy it the usual way. If 0.7.9's steps
above aren't done yet on a server, do those first.

### Upgrade notes: 0.7.11

No server-side steps. 0.7.11 adds the `import_member_photos` command for attaching member
photos in bulk; see "Bulk-importing member photos" below. Deploy it the usual way.

### Upgrade notes: 0.7.12

No manual steps. From this release on, every deploy writes the tag it deployed into the
server's `.env` as `IMAGE_TAG=` (see "Rollback"), so a by-hand `docker compose run … web` uses the
deployed image instead of an older local `:main`. The first 0.7.12 deploy sets it. Any
`IMAGE_TAG` value already in `.env` is replaced.

### Upgrade notes: 0.7.13

Run the usual deploy; `migrate` adds `Team.import_sources`. No other manual steps.

- **Redis 7.4 → 8.10.** `compose pull` fetches the new image and `web` reconnects to it. Redis
  only holds cache entries and the scheduler lease, so losing them on the restart does no harm.
- **Postgres stays on 17.** The 18 upgrade (dependabot #36) needs a dump/restore and a
  volume-path change. It is held back for a planned maintenance window.
- **New weekly job, `check_fixture_sources`** (Mondays 06:15). It runs inside the existing
  scheduler, so there is nothing to configure. It can be switched off from the control panel's
  Jobs tab like any other job.

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

## Migrations

Deliberately **not** run by the container's entrypoint. With more than one web container they
would race, and a starting gunicorn worker is a bad place to discover a failed migration.
Run them once, explicitly, as part of the deploy:

```bash
docker compose pull web
docker compose run --rm web python manage.py migrate
docker compose up -d --no-deps web
```

(`deploy/deploy-local.sh`, which `deploy/deploy-prod.sh` runs, does exactly this, plus a backup
first — see "Deploying updates". On the test server add `-f compose.behind-proxy.yaml`.)

## Database roles

The app does not connect as the Postgres superuser. Two roles:

| Role | Set by | Used by |
|---|---|---|
| `POSTGRES_USER` (default `rosterchief`) — superuser | `.env` (`POSTGRES_USER`/`POSTGRES_PASSWORD`) | the image's first-boot init, `deploy/backup.sh`, `deploy/restore-check.sh` (all via `docker compose exec db`, over the container's local socket) |
| `APP_DB_USER` (default `rosterchief_app`) — `NOSUPERUSER NOCREATEDB NOCREATEROLE` | `.env` (`APP_DB_USER`/`APP_DB_PASSWORD`) | `web` and every `manage.py` one-off, through `DJANGO_DATABASE_URL` in `.env.production` |

The app role **owns** the database and its `public` schema, so `migrate` can create and alter
tables; what it cannot do is anything outside that database — read other databases, create
roles, `COPY ... PROGRAM`, load extensions or files from the server. A SQL injection or a
leaked `DJANGO_DATABASE_URL` stays inside the app's own data.

### On a fresh volume

`deploy/postgres/10-app-role.sh` is mounted into `/docker-entrypoint-initdb.d`, so the first
`docker compose up -d db` on an empty `pgdata` volume creates the app role for you: a
`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS` role that owns the
database and its `public` schema, with `CONNECT` revoked from `PUBLIC`. Set
`APP_DB_USER`/`APP_DB_PASSWORD` in `.env` **before** that first start (an empty password makes
the init fail — see "First deploy"), and point `DJANGO_DATABASE_URL` at the same user/password.
The init script never runs again on an existing volume — changing those variables later does
nothing to the database.

### Converting an existing database

A database created before this existed is owned, table by table, by the superuser. Convert it
once — a few seconds, no downtime until the switch-over at the end. **Do it on the test server
first** (there, add `-f compose.behind-proxy.yaml` to every `docker compose` below and set
`COMPOSE="docker compose -f compose.behind-proxy.yaml"` for the backup), and **take a backup
first** — the first line below. Generate a password without URL-special characters so it can go
into `DJANGO_DATABASE_URL` as-is:

```bash
cd /home/deploy/RosterChief
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

-- Both should print zero rows. (The schema has no functions, types or extensions today; if
-- the second query ever lists some, ALTER FUNCTION/TYPE ... OWNER TO them the same way.)
SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) <> 'rosterchief_app';
SELECT p.proname, pg_get_userbyid(p.proowner) AS owner
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> 'rosterchief_app';
SQL
```

If `CREATE ROLE` fails with "already exists", someone got halfway before: the transaction
rolled back, so nothing changed — drop the `CREATE ROLE` statement (or `DROP ROLE
rosterchief_app;` if it owns nothing yet) and run it again. No default privileges or `GRANT`s
are needed: the app role owns every object, including the ones `migrate` creates later.

(Using a non-default `POSTGRES_USER`/`POSTGRES_DB`/`APP_DB_USER`? Substitute them for
`rosterchief`/`rosterchief`/`rosterchief_app` throughout.) Then switch the app over:

```bash
# .env            -> APP_DB_USER=rosterchief_app  APP_DB_PASSWORD=<APP_PW>
#                    (only documents it for a future fresh volume; the role already exists)
# .env.production -> DJANGO_DATABASE_URL=postgres://rosterchief_app:<APP_PW>@db:5432/rosterchief
docker compose run --rm web python manage.py migrate    # "No migrations to apply." -- proves the new URL, as the app role
docker compose up -d --no-deps --force-recreate web     # env_file changes need a recreate
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

- **Required outside `DJANGO_DEBUG=True`.** `deploy/deploy-local.sh` refuses to start a deploy
  when the line is missing or empty. A key that is present but malformed (or can't decrypt what's
  stored) is caught by the `authentication.E001` system check, which fails `migrate` and `check` —
  so the deploy stops before `web` restarts — and any TOTP sign-in would error. Local dev and the
  test suite derive a throwaway key from `DJANGO_SECRET_KEY` and need nothing.
- **Generate** (any of these; a Fernet key is 32 random bytes, url-safe base64):
  `python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`,
  or `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  where `cryptography` is installed (e.g. `uv run` in a checkout). Use a different key on the
  test server.
- **Back it up separately from the database** (e.g. in the password manager next to the
  backup key below). Lose it and every TOTP user is locked out of their authenticator *and*
  their recovery codes; a staff account then needs its authenticators deleted in the admin
  and re-enrolled. It sits next to the ciphertext in no backup, which is the point.
- **Rotate:** put the new key first, the old one after it, comma-separated
  (`MFA_ENCRYPTION_KEY=new,old`), then recreate `web` (`docker compose up -d --no-deps
  --force-recreate web`). New writes use the first; every key in the list decrypts. Existing
  values are not re-encrypted (only a user re-enrolling writes a fresh secret), so keep the old
  key in the list for good.

**Upgrading an existing deployment:** add `MFA_ENCRYPTION_KEY` to `.env.production` *before*
deploying the release that introduces it (0.7.8 — see "Upgrade notes: 0.7.8"). Migration
`authentication.0006_encrypt_mfa_secrets` then encrypts the existing plaintext secrets in place
(idempotent). Values not yet encrypted are still read correctly, so there is no window where
sign-in breaks. It is reversible with `manage.py migrate authentication 0005`, which decrypts
them again — required before rolling back to a release older than 0.7.8, which can't read the
encrypted form.

## Scheduled jobs

All fifteen platform jobs run from **one in-process scheduler inside `web` itself** —
`features/scheduler.py`, an APScheduler `BackgroundScheduler`. It is started in the gunicorn
**workers** (`gunicorn.conf.py`'s `post_worker_init` hook, stopped by `worker_exit`); a Redis
lease makes exactly one worker the one that runs the schedule, so a recycled worker hands it
over within seconds. It must never live in the gunicorn *master*: the master forks every worker,
and a lock held by any other thread at that instant is copied locked into the child — the
reason one worker once swallowed half the node's requests (see "A wedged web worker" below).
Without Redis (a dev box) every worker would lead. There is no host cron, no `worker`/`beat`, and no separate poller
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
| `sync_crew_invites` | hourly | creates any missing referee/official invites for upcoming home games (a safety net: eligibility can change after a game was scheduled -- a new referee/official, a level's teams, the officials feature switched on) |
| `poll_live_game_results` | every minute | refreshes score/live status from each game's competition data source, from 20 min before kickoff through 1h after its planned end (or until it's seen finishing — which also refreshes that game's teams' RBIHF standings) |
| `check_fixture_sources` | weekly, Monday 06:15 | re-checks each team's stored RBIHF/DFEL/IJNL fixture lists (`Team.import_sources`) for the current season and sends each club's admins one notification if games moved, appeared or disappeared, or a list could not be fetched — never imports anything itself |
| `refresh_standings` | daily 01:00 | recomputes the league standings of each team imported from RBIHF (current season, clubs with the `RBIHF` flag) for the public API — also refreshed per team the moment `poll_live_game_results` sees one of its games finish |
| `settle_game_timelines` | daily 01:30 | re-fetches the timelines (goals, penalties, …) of games that finished 3h–7 days ago until they stop changing, then marks them finalized — clubs with the `game_timeline` flag only (see GAME_TIMELINE.md) |
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
other ten act by default. `renew_subscriptions` also has a `--dry-run` to preview instead, for
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
`web` node (see "Going multi-server" below): every node runs the same scheduler code, but only the one
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
in a stdio buffer — `docker compose logs web` is where to look first if a run
seems stuck: the last line reached tells you whether it got past creating its own `JobRun` row (a
DB-connectivity problem from the very first write) or hung somewhere inside the command's own
work. `scheduler.leader_acquired`/`scheduler.leader_lost` lines show which node currently owns
the schedule, if there's more than one.

Each command still has its own `--help` for manual/dry-run use from a shell (`generate_seasons
--resync`, for one, is still CLI-only: it can delete rows, so it isn't something a schedule
should ever run unattended, Run now button included).

### Why not host cron, and why not Celery

Both were tried. **Celery** (`worker`/`beat`) measured **~740 MB** for `worker` alone on a real
1 GB box (prefork forking defeats Python's
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

## Bulk-importing member photos

`import_member_photos` attaches a directory of player pictures (`Member.player_photo`; a member's self-service profile picture is never touched) named after the licence on the club's
memberships (`12345.png`, `12345.jpg`, …) to the matching members. Copy the files to the
server, then run it in a one-off `web` container with the directory mounted read-only. The
running `web` container can't see host paths, and its root filesystem is read-only:

```bash
# on the server, from the checkout; photos/ holds 12345.png, 67890.jpg, ...
chmod -R a+rX photos        # the container runs as an unprivileged user
docker compose run --rm --no-deps -v "$PWD/photos:/import:ro" web \
    python manage.py import_member_photos <club-slug> /import --dry-run
docker compose run --rm --no-deps -v "$PWD/photos:/import:ro" web \
    python manage.py import_member_photos <club-slug> /import
```

- `docker compose run` starts the image named by `IMAGE_TAG` in `.env`, which each deploy sets
  to the tag it deployed (from 0.7.12 on). "Unknown command: 'import_member_photos'" means
  `.env` still points at an older image. Check with `grep IMAGE_TAG .env`, or put the release
  in front of the command: `IMAGE_TAG=v0.7.12 docker compose run …`.
- `<club-slug>` is the club's subdomain. Licences are matched in that club only, across every
  season, ignoring case.
- A member who already has a player picture is skipped unless you add `--replace`.
- Files whose licence matches nobody, or more than one member, and files that aren't readable
  images are listed and skipped. The rest are still imported, so fix those files and run it
  again; members already done are skipped the second time.
- By default it leaves **Show the player picture on the club's public website** alone, so imported
  photos are visible to the member, their family and staff only. Add `--public` to turn it on
  for every member whose photo the run stores. Only do that when the club has public-photo
  consent for the whole batch.
- Photos go to private storage, which the nightly backup already covers. Delete `photos/`
  afterwards.

### A wedged web worker

**Symptom:** a share of requests hang until the client gives up (the rest answer in milliseconds),
`docker compose ps` shows `web` **unhealthy**, and the access log goes quiet: gunicorn logs a
request only when it *finishes*, so a hung request leaves no line. **Cause it has had:** a worker
forked while another thread held a lock (the DNS resolver's `getaddrinfo`, a Redis pool's) came up
with that lock stuck, and hung forever the first time it needed it — for the DNS lock, the first
time a database connection expired (`CONN_MAX_AGE`, 60 s) and had to be reopened.

**Check, from the server** (`docker compose exec -T web ...`; the image has no `ps`/`ss`):

- Plain requests to `http://localhost:8000/healthz`: a healthy node answers every one with a 301 in
  about 1 ms. Some answering and some timing out means one worker is wedged.
- Sockets stuck in `CLOSE_WAIT` on `:8000` (read `/proc/net/tcp`, port `1F40`) are connections the
  wedged worker accepted and never served; `/proc/<pid>/fd` says which worker owns them.
- `gunicornc -s /tmp/.gunicorn/gunicorn.ctl -c "show workers"` shows the arbiter's view (it still
  looked healthy). To see *what* the threads are blocked on, run
  `python -c "import sys; sys.remote_exec(<worker pid>, '/tmp/dump.py')"` with a script that calls
  `faulthandler.dump_traceback(file=..., all_threads=True)` (Python 3.14's safe remote debugging).

**Fix:** `docker compose restart web` clears it. Do not start threads from a master-side gunicorn
hook (`when_ready`, `on_starting`, `pre_fork`, …); `features.tests.GunicornConfigTests` fails if
one appears. The database and Redis also have connect/read timeouts now, so a stalled backend fails
a request instead of holding its thread.

## Maintenance mode

Control panel → **Features → Maintenance mode**. While it is on:

- every **club subdomain** serves a 503 maintenance page, in that club's own colours;
- the **control panel and the sign-in screens stay open**, because closing them would leave
  you with no way to turn it back off;
- `/healthz` keeps answering on every host, or the load balancer would take the node out of
  rotation and the control panel with it;
- the **scheduled jobs stand down** — the fifteen jobs in the "Scheduled jobs" table, plus
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

`deploy/deploy-local.sh` (and so `deploy/deploy-prod.sh`, which runs it over `ssh -t`) does this
for you: it enters maintenance mode before the backup and migration, and once the new image is
healthy asks whether to reopen the platform (Enter = yes). Answering anything but `y` -- or a failed deploy,
or no terminal -- leaves it closed and prints the command to reopen it (also available from
the control panel). `NO_MAINTENANCE=1` skips the whole thing; `MAINTENANCE_MESSAGE="..."` sets
what clubs see. `deploy/deploy-prod.sh --reopen` (or `REOPEN_MAINTENANCE=1`) reopens the platform by itself once the deploy is healthy, with no prompt -- for unattended deploys; a failed deploy still leaves it closed. A first deploy (no running web yet) has nothing to close and skips it.

The state lives in Redis as well as the database, so it takes effect on **every worker and
every server at once** — a per-process cache would leave some workers still serving clubs.

## Native app push (APNs)

The native iOS app gets its push notifications straight from **Apple's APNs**
(token-based auth, HTTP/2) — no cloud intermediary. The server only needs one `.p8` key from
the Apple Developer account. Android is not served yet.

Do it once per server: production and the test server share the same key and bundle ID, but
each keeps its own `.env`. A phone registers with exactly one server (the base domain it
signed in on), so pushes never double up.

### Step 1 — Create the key

1. [developer.apple.com](https://developer.apple.com/account) → **Certificates, Identifiers
   & Profiles** → **Keys** → **+**. Name it (e.g. `RosterChief push`), tick **Apple Push
   Notifications service (APNs)** and download the `.p8` file. Apple lets you download it
   **once**; keep it safe.
2. Note the **Key ID** (10 characters, shown on the key) and your **Team ID** (top right of
   the account page, or *Membership details*).
3. The app's identifier must have the **Push Notifications** capability. The bundle ID is
   `app.rosterchief.RosterChief` unless the app changes it.

One key works for both the sandbox and production APNs hosts.

### Step 2 — Configure the server

In `.env.production` (`.env.example` lists the names):

```bash
APP_PUSH_APNS_KEY_ID=ABC123DEFG
APP_PUSH_APNS_TEAM_ID=1A2B3C4D5E
APP_PUSH_APNS_BUNDLE_ID=app.rosterchief.RosterChief
# The .p8 file's text on one line with \n for the line breaks ...
APP_PUSH_APNS_KEY=-----BEGIN PRIVATE KEY-----\nMIGT...\n-----END PRIVATE KEY-----
# ... or mount the file into the container and point at it instead:
# APP_PUSH_APNS_KEY_FILE=/run/secrets/apns.p8
```

Redeploy (`deploy/deploy-prod.sh`). Phones that registered earlier start receiving pushes as
soon as the app registers again, which it does on every launch.

Live Activities need nothing extra on the server (they use the same key; the `.push-type.liveactivity`
topic is derived from the bundle ID). If the app's `ActivityAttributes` type is not called `GameAttributes`, set
`APP_PUSH_APNS_ACTIVITY_ATTRIBUTES` to its name.

A debug build run from Xcode sends `environment: "sandbox"` and is delivered through Apple's
sandbox host; TestFlight and App Store builds use production. The server picks the host from
that value, so nothing else needs configuring.

### Step 3 — Check it

Open the app on a phone, sign in and allow notifications. Then, on the server:

```bash
docker compose exec web python manage.py shell -c "
from appapi.models import PushDevice
from appapi.push import get_gateway
gateway = get_gateway()
for device in PushDevice.objects.filter(user__email='you@example.com', enabled=True):
    print(device.installation_id, device.environment, gateway.supports(device), gateway.publish(device, title='Test', body='Push works', data={}))
"
```

Each line should end in `True True` and the phone should buzz. What the output means:

| You see | Cause |
|---|---|
| no lines | the app never registered (not signed in, or notifications refused), or the device was disabled after Apple reported its token dead. It re-registers on the next launch |
| `False` in the third column | no APNs key configured (the gateway is the do-nothing one), or the device is not iOS |
| `False` in the fourth column | Apple says the token is dead (`BadDeviceToken`/`Unregistered`): usually a debug build's token sent to the production host or the other way round. Check the build's `environment` |
| an `ApnsError` | Apple refused the request: `InvalidProviderToken` = wrong Key ID/Team ID/key, `TopicDisallowed` = the bundle ID doesn't match the key's team, `TooManyProviderTokenUpdates` = restart loops |
| `True True` but nothing arrives | notifications are off for the app in iOS Settings, or Focus is on |

### Rotating or revoking

Create a new key (step 1), put its Key ID and text in `.env.production`, redeploy, then
revoke the old key on the Apple Developer site.

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

	# Only if the records are proxied through Cloudflare: trust CF-Connecting-IP only from
	# Cloudflare's own ranges, so {client_ip} is the visitor. Copy the full, current list from
	# deploy/caddy/Caddyfile (source: https://www.cloudflare.com/ips/). Global: it applies to
	# every site on this Caddy. Without it, {client_ip} is simply the connecting address.
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
	encode zstd gzip
	request_body {
		max_size 64MB
	}
	reverse_proxy 127.0.0.1:8001 {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Real-IP {client_ip}
	}
}
```

### What actually goes wrong

1. **The token must cover the *new* zone.** A Cloudflare token is scoped to named zones, and
   an existing one almost certainly grants `Zone:DNS:Edit` (+ `Zone:Zone:Read`) on the domain
   it was made for and nothing else. The new site then fails its DNS-01 challenge on a
   permissions error whose text does not say so. Widen the token, or mint a second one and use the snippet form.
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

5. **`header_up X-Forwarded-Proto` and `header_up X-Real-IP {client_ip}` are not optional**,
   exactly as in the bundled Caddyfile: without the first Django believes the request behind
   the proxy is plain HTTP; without the second a client's own `X-Real-IP` header reaches Django
   and chooses the address the sign-in rate limits see. Keep the `request_body { max_size … }`
   cap too: it rejects oversized uploads (413) before they tie up a gunicorn worker. The bundled
   Caddyfile explains the value.

6. **Give the test instance its own subdomain tree** and set
   `ROSTERCHIEF_BASE_DOMAIN=test.rosterchief.app` in its `.env.production`, with
   `DJANGO_ALLOWED_HOSTS=.test.rosterchief.app`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://test.rosterchief.app`
   and its own `MFA_ENCRYPTION_KEY`. `ROSTERCHIEF_BASE_DOMAIN` drives tenant resolution, the
   shared session cookie *and* the WebAuthn RP ID — point it at the production domain and test
   passkeys start colliding with real ones.

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

Make these **DNS only** (grey cloud) unless you pay for Cloudflare's Advanced Certificate
Manager: Cloudflare's free edge certificate covers `rosterchief.app` and `*.rosterchief.app`,
one level deep, so a proxied `ajax.test.rosterchief.app` fails in the browser with a
certificate error before it ever reaches your Caddy. DNS-only records still get their
certificate from Caddy's DNS-01 challenge as usual.

The compose project is named `rosterchief-test`, so its containers and volumes never collide
with a production stack on the same host.

### Deploying with one command

First-time setup on the server, once (Docker and git as in "First deploy → Preparing the
server"):

```bash
git clone https://github.com/bsiebens/RosterChief.git /home/deploy/RosterChief
cd /home/deploy/RosterChief
cp .env.compose.example .env             # POSTGRES_PASSWORD, APP_DB_USER/APP_DB_PASSWORD (WEB_PORT if not 8001)
cp .env.production.example .env.production   # test.rosterchief.app values -- see point 6 above
chmod 600 .env .env.production
# then add the reverse_proxy site block to the host's Caddy (see above)
```

The `ghcr.io/bsiebens/rosterchief` package is currently **public** (anonymous pulls work), so
no registry login is needed. If it is ever made private, the server needs a one-time login
before its first pull — a GitHub personal access token with `read:packages` is enough:

```bash
echo "<token>" | docker login ghcr.io -u <your-github-username> --password-stdin
```

Public is reasonable here: the image contains no secrets, only application code and
dependencies (all secrets are `.env`/`.env.production`, never baked in).

After that, `deploy/deploy-dev.sh` does a full deploy over SSH (the first run also creates the
database; then create a superuser with
`docker compose -f compose.behind-proxy.yaml run --rm web python manage.py createsuperuser`):

```bash
deploy/deploy-dev.sh                    # deploy the current branch
BRANCH=development deploy/deploy-dev.sh # what CI itself deploys automatically -- see below
deploy/deploy-dev.sh --push             # push the branch first, then deploy
```

It runs from your machine and does the work on the server in one SSH session: check that
`.env` and `.env.production` exist, `git fetch` + `reset --hard origin/<branch>` (a deploy
target only receives deploys; the checkout matters for `compose.behind-proxy.yaml` and
`deploy/postgres/`, the app code comes from the image), pull `:<branch>`, `up -d db redis`, run
migrations *explicitly*, recreate only `web`, and poll `http://127.0.0.1:<WEB_PORT>/healthz`
(`WEB_PORT` read from the server's `.env`). The pull, the migration and the restart all use
the branch's image (`IMAGE_TAG=<branch>`, exported for the whole run). Unlike production: no backup, no env-key preflight
(a missing `MFA_ENCRYPTION_KEY` surfaces as a failed `migrate`), and it never touches the host's
Caddy.

It refuses to deploy a branch whose local commits are not pushed — the server pulls from git,
so unpushed work would ship stale code silently. The host, user, directory, branch, and SSH
identity come from `SSH_HOST` / `SSH_USER` / `REMOTE_DIR` / `BRANCH` / `SSH_KEY_FILE`; unlike
`deploy-prod.sh` it has built-in defaults (the maintainer's own dev box, the current branch) —
see "Deploying updates" above for keeping them in `deploy/.env.dev.local`.

#### Where the image comes from

The app image is **not** built on the server. `.github/workflows/build-and-push.yml` (workflow
`CI`) builds it on GitHub's own runners on every push to `development` or `main` and pushes it
to `ghcr.io/bsiebens/rosterchief`, tagged `:<branch>` and `:<branch>-<short-sha>` — plus, on
`main` only, `:vX.Y.Z` (see "Branching & release model" above). The server only ever
`docker compose pull`s: building the image on a small box runs it out of memory.

**Nothing reaches the registry untested.** The same workflow runs jobs in order:

1. `test` — `ruff check` + the full suite. Runs on every push *and* every pull request, against
   any branch. The suite is split over four parallel `test (N/4)` jobs
   (`uv run python manage.py test --shard=N/4`, split by test class); `test` itself only gathers
   their results, so it stays the one check name branch protection and `build` wait on.
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

**How much of the suite, and on which database.** Production runs Postgres, the quick feedback
loop runs the in-memory sqlite `rosterchief/test_runner.py` uses locally, and the full suite takes a
few minutes on a hosted runner. So each event runs the suite **once**, in the tier and on the
database that fits it (a small `plan` job decides):

| Event | Tests | Database |
|---|---|---|
| pull request into `main` (every release/hotfix) | **full**, 4 shards | Postgres: a Postgres-only break is caught *before* it merges |
| push to `main` | full, 4 shards | Postgres |
| nightly (`schedule`, 05:00 UTC, on `main`) | full, 4 shards | Postgres |
| push to `development` | **fast**, 2 shards | Postgres |
| any other pull request (a feature branch into `development`) | fast, 2 shards | sqlite |

The **fast tier** (`manage.py test --fast`) skips the tests listed in
`rosterchief/fast_suite_skip.txt`: those whose every covered line is also covered by another test.
Roughly a third of the suite then reproduces the whole suite's line coverage, in two jobs instead
of four, which about halves the billed CI time of a feature-branch run. Nothing is deleted, and a release can never reach `main` without the full suite
passing on its PR. The list is generated and **verified** by `deploy/refresh_fast_suite.py` (~5
minutes: it runs the full suite under per-test coverage, picks a covering set, then runs the fast
tier and checks it hits every line the full suite hit). A test that is not in the list always runs, so
a newly written test is never skipped; regenerate the list now and then and after big refactors. A
stale list only makes the fast tier a little less representative. Caveat: line coverage says the code
ran, not that every behaviour was asserted, which is why `main`, releases and the nightly run
everything. A scheduled run never builds, tags or deploys anything, and there is no separate
Postgres workflow.

**`test` is a required status check on both `development` and `main`** —
`deploy/configure-branch-protection.sh` sets this (and the rest of branch protection) from the
command line; see "Branching & release model" above for what it configures and why.

**Auto-deploying the dev instance.** The `deploy-dev` job runs `deploy/deploy-dev.sh` over SSH
the same way a person would, and is skipped entirely unless these repo secrets are set
(Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `DEV_SSH_HOST` | the dev server's host/IP (the box this section is about) |
| `DEV_SSH_USER` | the SSH user on that box |
| `DEV_SSH_KEY` | a private key authorized on that box, scoped to deploys only |
| `DEV_REMOTE_DIR` | the checkout path there, e.g. `/home/deploy/RosterChief` |

Worth tightening later: these currently live as plain repo secrets, readable by any workflow
run regardless of which branch triggered it. Moving them into a GitHub **Environment** named
`development` (Settings → Environments), and adding `environment: development` to the
`deploy-dev` job, scopes them so only a run deploying to that environment can read them —
free, and a real reduction in blast radius if a workflow file is ever compromised via a
malicious PR (repo secrets are available to `pull_request_target`/workflow-dispatch runs in a
way branch-restricted environment secrets are not). Not done here only because it's a UI step
this document can describe but not perform for you.

Production is deliberately **not** wired to auto-deploy — `deploy/deploy-prod.sh` stays a
human's call, with its own env preflight, backup-before-migrate and main-or-release-tag-only
guardrails intact. If
that ever changes, gate it behind a `production` Environment with **required reviewers** —
the one GitHub feature that makes "auto-deploy to prod" mean "one click to approve", not "one
merge to prod."

**Deploying a specific version**: `deploy/deploy-dev.sh` always pulls `:<branch>` (latest for
that branch) — fine for the dev instance, which is meant to track `development`'s tip. For
production, deploy the release tag, never a floating branch tag:
`deploy/deploy-prod.sh --version v0.5.0` (see "Deploying updates"). It checks out the same tag
on the server, so the compose file and deploy scripts match the image.

`:main-<short-sha>` (or `:development-<short-sha>`) pins an exact build that was never tagged
as a release — short SHAs (7 characters) come from the GitHub Actions run or
`git log --oneline`. There's no `deploy-prod.sh` flag for it; log in, check out that commit, and
run `IMAGE_TAG=main-<short-sha> deploy/deploy-local.sh` (see "Deploying while logged into the
server"). Don't edit `IMAGE_TAG` in the server's `.env`; each deploy records the tag it
deployed there itself — see "Rollback".

## Backups

Everything that carries state is backed up by one script, `deploy/backup.sh`, run nightly by
root's cron on the production server — and before every deploy, as the deploy user (see
"Deploying updates"):

| What | File |
|---|---|
| The database | `db-<date>.sql.gz.age` |
| Public uploads (logos, images) — only while they're on local disk | `media-<date>.tar.gz.age` |
| Private uploads (member photos, onboarding documents, medical certificates) | `private-media-<date>.tar.gz.age` |

Each file is **encrypted** on the server with [age](https://age-encryption.org) before it is
stored or copied anywhere. The server only has the *public* key: it can encrypt, but nobody who
gets hold of the server or the backup location can read the backups. The *private* key, which
decrypts them, lives only with you.

`<date>` is `YYYY-MM-DD-HHMM`. The files are kept for 14 days on the server
(`/var/backups/rosterchief`) and — the part that actually protects you — copied every night to
a **second server over SSH**, where they are kept for 60 days. A backup on the same disk as
the database survives a bad migration, not a dead server.

**The offsite copy is append-only from production's side.** Whoever breaks into the production
server gets its upload key too, and a key that can delete backups lets them wipe those as well
before they encrypt or delete the live data. So:

1. Production uploads into one folder on the backup server, `~rosterchief-backup/rosterchief/incoming`
   (`BACKUP_REMOTE=offsite:rosterchief/incoming`), with a key whose `sftp-server` refuses to
   remove, rename or change any file — it can only create files and write to them.
2. Every 15 minutes a root job on the backup server (`deploy/backup-server/archive-incoming.sh`)
   copies each finished upload into `/var/lib/rosterchief-backups/archive` — root-only; the
   upload account can't even list it — and deletes it from `incoming`. Pruning after 60 days
   happens there too, not from production.
3. Restores fetch from that archive with your own admin login on the backup server, never with
   the upload key.

The one thing a stolen upload key can still do is overwrite a file in `incoming` in the few
minutes before it is archived (open-for-writing can't be denied without denying uploads), or
fill the disk. The archive itself is out of its reach.

Setting it up is a one-time job across three machines — your computer, the production server
and the backup server. Do the steps in order.

### Installing the tools

`age` (encryption) and `rclone` (the upload) are needed on the **production server**; `age` also
on **your own computer**. The backup server needs a working SSH server, `cron` and `flock`
(util-linux, on every Linux).

**Debian / Ubuntu** (Debian 12+, Ubuntu 22.04+):

```bash
sudo apt install age rclone
```

(Debian 12's `rclone` is 1.60, which has no `--inplace` flag and doesn't need one — it already
writes uploads straight to their final name. `backup.sh` checks the version and adds the flag
from 1.63 on.)

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

Amazon Linux 2023 also ships **without cron** — on the production server *and* on a backup
server that runs Amazon Linux. Install and start it before step 5:

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

### Step 2 — On the backup server: an upload account and a root-only archive

The backup server can be any Linux machine you can reach over SSH (a small VPS, a NAS, a box at
home) where **you have your own login with `sudo`** — that login, not the upload account, is
how you'll get backups back out. Log into it.

1. **The upload account.** It receives files into `incoming` and nothing else:

   ```bash
   sudo useradd --create-home rosterchief-backup     # no password: key login only
   sudo -u rosterchief-backup mkdir -p ~rosterchief-backup/rosterchief/incoming
   # .ssh belongs to root: the account can write files, and must not be able to rewrite its
   # own authorized_keys (which would drop the restrictions from step 3.4)
   sudo mkdir -p ~rosterchief-backup/.ssh
   sudo touch ~rosterchief-backup/.ssh/authorized_keys
   sudo chown root:root ~rosterchief-backup/.ssh ~rosterchief-backup/.ssh/authorized_keys
   sudo chmod 755 ~rosterchief-backup/.ssh
   sudo chmod 644 ~rosterchief-backup/.ssh/authorized_keys
   ```

   (`useradd` exists on every distro, Amazon Linux included; the account gets no password, so
   it can only be reached with the SSH key added in step 3. sshd accepts an `authorized_keys`
   owned by root as well as by the account.)

2. **The archive**, owned by root and closed to everyone else:

   ```bash
   sudo install -d -m 700 -o root -g root /var/lib/rosterchief-backups /var/lib/rosterchief-backups/archive
   ```

3. **The archive job.** The backup server doesn't need a checkout of the repo; copy the one
   script over from yours, then install it root-only:

   ```bash
   # on your own computer, from the checkout
   scp deploy/backup-server/archive-incoming.sh <you>@<backup-host>:
   # on the backup server
   sudo install -m 700 -o root -g root ~/archive-incoming.sh /usr/local/sbin/rosterchief-archive-incoming
   rm ~/archive-incoming.sh
   sudo MIN_AGE_MINUTES=0 /usr/local/sbin/rosterchief-archive-incoming && echo ok   # nothing to do yet: prints "ok"
   ```

   What it does, per run: every `*.age` file in `incoming` that hasn't been written to for 10
   minutes and starts with age's header (`age-encryption.org/v1`) is copied into the archive as
   a new root-owned file, compared with the original, and only then deleted from `incoming`. A
   file with the same name already in the archive is never overwritten — an identical re-upload
   is dropped, a different one is kept beside it as `<name>.dup-<time>.age` with a warning.
   Anything named `*.age` that isn't an age file goes to `/var/lib/rosterchief-backups/rejected`
   with a warning. Archived (and rejected) files older than `ARCHIVE_KEEP_DAYS` (60) are
   deleted. All settings are environment variables, documented at the top of the script. The
   cron line comes in step 5.

Leave this session open; you'll paste a key into it in step 3.

### Step 3 — On the production server: age, rclone, SSH key, config

Log into the production server. The nightly backup runs as **root** (from root's crontab), so
run these with `sudo -i` first.

1. **Install the tools:** `age` and `rclone` — see "Installing the tools" above (Amazon
   Linux needs the release binaries and `cronie`). If you haven't already (see "First deploy →
   Preparing the server"), make the backup directory belong to the deploy user, so the backup
   every deploy takes can write there too — root's cron job can write to it either way:

   ```bash
   mkdir -p /var/backups/rosterchief
   chown deploy: /var/backups/rosterchief && chmod 700 /var/backups/rosterchief
   ```

2. **Tell the backup which key to encrypt to.** Add the public key from step 1 to the compose
   `.env` in the checkout (the same file that holds `POSTGRES_PASSWORD`):

   ```bash
   cd /home/deploy/RosterChief
   echo 'BACKUP_AGE_RECIPIENT=age1…your public key…' >> .env
   ```

3. **Make an SSH key for the backup upload** (no passphrase: cron has to use it unattended):

   ```bash
   ssh-keygen -t ed25519 -N "" -C "rosterchief backups" -f /root/.ssh/rosterchief_backup
   cat /root/.ssh/rosterchief_backup.pub
   ```

4. **Allow that key on the backup server — for uploading files only.** In the step-2 session
   on the backup server, put the printed line into `~rosterchief-backup/.ssh/authorized_keys`,
   prefixed so the key can't open a shell (`restrict`), can only talk SFTP
   (`command="…sftp-server"`), and can't remove, rename or change files (`-P`, the list of SFTP
   requests `sftp-server` refuses):

   | Denied | So the key can't… |
   |---|---|
   | `remove`, `rmdir` | delete files or folders |
   | `rename`, `posix-rename` | rename a backup away, or rename over one |
   | `setstat`, `fsetstat`, `lsetstat` | truncate a file, change its permissions or its timestamps (which would make the archive job take a half-written file, or pruning delete a fresh one) |
   | `symlink`, `hardlink` | plant links that point elsewhere |
   | `copy-data` | copy one file over another server-side |

   Everything else — `open`, `write`, `read`, `stat`, `mkdir`, listing — still works, which is
   all an upload needs. Those names are what `sftp-server -Q requests` prints; `-P` has been in
   OpenSSH since 6.5 (Amazon Linux 2023 ships 8.7, Debian 12 ships 9.2). A name your version
   doesn't know (`copy-data` is new in 9.0) is simply never matched — harmless.

   The `sftp-server` path differs per distro, and a wrong path shows up later as an `sftp`
   login that connects and then immediately says **`Connection closed`**. Check which one
   exists on the backup server:

   ```bash
   ls /usr/libexec/openssh/sftp-server /usr/lib/openssh/sftp-server 2>/dev/null
   ```

   Then add the key with that path — **Amazon Linux / RHEL / Fedora**:

   ```bash
   echo 'restrict,command="/usr/libexec/openssh/sftp-server -P remove,rmdir,rename,posix-rename,setstat,fsetstat,lsetstat,symlink,hardlink,copy-data" ssh-ed25519 AAAA…the key… rosterchief backups' \
     | sudo tee -a ~rosterchief-backup/.ssh/authorized_keys
   ```

   or **Debian / Ubuntu**:

   ```bash
   echo 'restrict,command="/usr/lib/openssh/sftp-server -P remove,rmdir,rename,posix-rename,setstat,fsetstat,lsetstat,symlink,hardlink,copy-data" ssh-ed25519 AAAA…the key… rosterchief backups' \
     | sudo tee -a ~rosterchief-backup/.ssh/authorized_keys
   ```

   The file stays root-owned, mode 644, as set up in step 2 (SSH ignores it if the account or
   anyone else can write to it). No other key belongs in this file.

5. **Trust the backup server's host key, and check the key's limits** (back on the production
   server, as root). Compare the fingerprint it shows with the one the backup server reports
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` there) before answering `yes`:

   ```bash
   sftp -i /root/.ssh/rosterchief_backup rosterchief-backup@<backup-host>
   # answer "yes" if the fingerprint matches. At the sftp> prompt:
   #   ls rosterchief          -> shows "incoming"
   #   rmdir rosterchief/incoming   -> must say "Permission denied"  (the -P list works)
   #   bye
   # (Plain ssh gets no shell with this key -- that's intended.)
   ```

   Run it **as root** (`sudo -i` first): the key under `/root/.ssh` isn't readable by
   `ec2-user`/your normal login, and the host key has to land in *root's* `known_hosts`, which is
   what the nightly backup uses. If it fails:

   - `Identity file … not accessible: Permission denied` → you're not root.
   - `Permission denied (publickey…)` → the key line in `authorized_keys` doesn't match
     `/root/.ssh/rosterchief_backup.pub`, or the ownership/permissions from step 2 are off
     (`authorized_keys` and `.ssh` must not be writable by anyone but their owner).
   - Connects, then immediately `Connection closed` → the `sftp-server` path in
     `authorized_keys` doesn't exist on the backup server (see step 4).
   - `rmdir` **succeeds** → the `-P …` part is missing from the key line: fix it (and recreate
     `incoming` as in step 2).
   - The backup server's log says exactly why: `sudo journalctl -u sshd -n 20` (Amazon Linux)
     or `sudo tail /var/log/auth.log` (Debian/Ubuntu). With `-l VERBOSE` added after `sftp-server` in
     the key line, every refused request is logged there too (`Refusing denylisted … request`).

6. **Create the rclone remote** called `offsite`:

   ```bash
   rclone config create offsite sftp \
     host <backup-host> user rosterchief-backup \
     key_file /root/.ssh/rosterchief_backup \
     known_hosts_file /root/.ssh/known_hosts \
     shell_type none set_modtime false
   rclone lsd offsite:rosterchief          # should list the "incoming" folder
   ```

   `shell_type none` because the key may only do file transfer; `set_modtime false` because
   setting a file's time is one of the denied requests (so a hand-run `rclone copy` works too);
   add `port <n>` if SSH on the backup server isn't on 22.

### Step 4 — Test it once by hand

Still as root on the production server:

```bash
cd /home/deploy/RosterChief
BACKUP_REMOTE=offsite:rosterchief/incoming deploy/backup.sh /var/backups/rosterchief
ls -l /var/backups/rosterchief                 # db-…sql.gz.age etc. -- only .age files are new
rclone ls offsite:rosterchief/incoming         # the same files on the backup server
rclone deletefile offsite:rosterchief/incoming/<one of them>   # must fail: "permission denied"
```

The script prints `backup ok: …` and `copied N file(s) to offsite:rosterchief/incoming`. If it
stops with an error, fix what it says and run it again: every file is written under a `.part`
name and only renamed once complete, so a half-written file never looks like a backup
(leftover `.part` files are removed by a later run after a day). One exception: if `age` itself
fails, the unencrypted archive it was encrypting stays in `/var/backups/rosterchief` — delete
it. Only `*.age` files are ever copied offsite.

Which files have gone offsite is recorded in `/var/backups/rosterchief/.offsite-sent` — the
backup server's `incoming` empties itself, so rclone can't tell by looking there. Each run
sends only the `*.age` files not on that list: a night the upload fails is made up the next
night, and the backup a deploy takes goes out with the next nightly run. Delete the file to
send everything still on the server again (the archive job drops identical re-uploads).

Then on the backup server, archive them right away instead of waiting 10 minutes:

```bash
sudo MIN_AGE_MINUTES=0 /usr/local/sbin/rosterchief-archive-incoming
sudo ls -l /var/lib/rosterchief-backups/archive    # the files are here, owned by root
sudo ls -l ~rosterchief-backup/rosterchief/incoming   # ...and gone from here
```

If an upload fails:

- `Update Create failed: permission denied` → `incoming` doesn't exist or isn't owned by
  `rosterchief-backup`, or `BACKUP_REMOTE` points somewhere else (it must end in
  `/incoming`; the script says so when it doesn't).
- `Rename failed: permission denied`, and a `<name>.<hash>.partial` file left in `incoming` →
  rclone was run without `--inplace` (from 1.63 on it uploads to a temporary name and renames
  it by default). `backup.sh` adds the flag itself; add it when you run rclone by hand. The
  archive job removes the stray `.partial` after a day.
- `SetModTime failed: permission denied` → the remote lacks `set_modtime false`:
  `rclone config update offsite set_modtime false` (`backup.sh` also passes
  `--sftp-set-modtime=false`).
- `Failed to delete … permission denied` from `rclone delete` → working as intended. Nothing on
  the production server deletes offsite backups any more.

### Step 5 — Schedule it (on both servers)

`crontab -e` as root on the **production server**:

```cron
MAILTO=you@example.com
# cron's default PATH lacks /usr/local/bin (where age lives on Amazon Linux) -- keep this line.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Nightly at 02:30: dump, encrypt, keep 14 days here, upload anything not yet sent.
30 2 * * *  cd /home/deploy/RosterChief && BACKUP_REMOTE=offsite:rosterchief/incoming deploy/backup.sh /var/backups/rosterchief
```

`sudo crontab -e` on the **backup server** (install `cronie` first on Amazon Linux):

```cron
MAILTO=you@example.com
# Every 15 minutes: archive finished uploads out of the upload account's reach, prune after 60 days.
# Routine lines go to the log; warnings and errors also to stderr, so cron mails only those.
*/15 * * * *  ARCHIVE_LOG=/var/log/rosterchief-archive.log /usr/local/sbin/rosterchief-archive-incoming
```

`MAILTO` only works if the server can send mail (a local MTA such as `postfix`/`msmtp`); if it
can't, at least check `/var/backups/rosterchief` for fresh files after the first night, or
append `>> /var/log/rosterchief-backup.log 2>&1` to the production line and check that log —
and on the backup server, check `/var/log/rosterchief-archive.log` and
`sudo ls -lt /var/lib/rosterchief-backups/archive | head`.

Knobs, all optional:

- production line: `KEEP_DAYS=14` (on the server; files older than that are deleted locally).
  `BACKUP_REMOTE` itself is only read from the environment, so it stays on the cron line;
  `BACKUP_AGE_RECIPIENT`, `POSTGRES_USER` and `POSTGRES_DB` come from the environment or else
  from `.env` in the current directory — hence the `cd`. `REMOTE_KEEP_DAYS` is gone: the
  production server can't delete offsite files, and the script prints a note if it's still set.
- backup-server line: `ARCHIVE_KEEP_DAYS=60` (`0` keeps everything forever),
  `MIN_AGE_MINUTES=10` (how long an upload must sit untouched before it's taken).

Cron mails `MAILTO` whenever a script fails, and both fail loudly on purpose: a backup that
fails quietly is worse than none. With `BACKUP_REMOTE` set and no `BACKUP_AGE_RECIPIENT`,
`backup.sh` refuses to run at all rather than copy plaintext dumps off the box. The archive job
exits non-zero (so: a mail) for a rejected or conflicting upload — look at it; on a quiet night
nothing but the production backup should ever land in `incoming`.

Remove the old weekly `deploy/restore-check.sh` cron line if you have one: the server can no
longer decrypt the backups (by design), so the restore test moves to your own computer (next
step).

Every deploy also takes a backup first (`deploy/deploy-prod.sh` → `deploy-local.sh`, as the
deploy user); it's encrypted the same way and lands in the same directory, but isn't copied
offsite by the deploy itself — the next nightly run copies it along with its own files.

### Step 6 — Prove a backup restores (monthly, on your own computer)

A backup you've never restored is a hope, not a backup. On your own computer, with a checkout
of this repo and Docker. The backups come from the **archive** on the backup server, fetched
with your own admin login there (`<you>@<backup-host>`, the account with `sudo` from step 2) —
the upload account can't see the archive, and no key other than production's belongs on it.

**Each time:**

```bash
# 1. fetch last night's database dump from the backup server's archive
mkdir -p restore-test
ssh <you>@<backup-host> 'sudo ls -lt /var/lib/rosterchief-backups/archive | head'
#    note the newest db-….sql.gz.age name, then:
ssh <you>@<backup-host> 'sudo cat /var/lib/rosterchief-backups/archive/db-2026-07-14-0230.sql.gz.age' \
  > restore-test/db-2026-07-14-0230.sql.gz.age

# 2. start a throwaway local Postgres (its own env file, so your dev .env is left alone;
#    compose also insists that .env.production exists, even empty) and restore into it
printf 'POSTGRES_PASSWORD=restore-test\nAPP_DB_PASSWORD=restore-test\n' > restore-test/compose.env
[ -f .env.production ] || touch .env.production
docker compose -f compose.behind-proxy.yaml --env-file restore-test/compose.env up -d db
#    the private key: export it from the password manager to a file first
COMPOSE="docker compose -f compose.behind-proxy.yaml --env-file restore-test/compose.env" \
  BACKUP_AGE_IDENTITY=~/rosterchief-backup-key.txt deploy/restore-check.sh ./restore-test

# 3. clean up: the database, the dump and the key file all hold (access to) personal data
docker compose -f compose.behind-proxy.yaml --env-file restore-test/compose.env down -v
rm -rf restore-test && rm ~/rosterchief-backup-key.txt
```

If `sudo` on the backup server asks for a password, the one-line `ssh … 'sudo …'` commands
can't answer it. Use `ssh -t` for the listing, and fetch in three steps instead (the file is
encrypted, so a moment in your home directory there is fine):

```bash
ssh -t <you>@<backup-host> 'sudo install -m 600 -o "$USER" /var/lib/rosterchief-backups/archive/db-2026-07-14-0230.sql.gz.age ~/'
scp <you>@<backup-host>:db-2026-07-14-0230.sql.gz.age restore-test/
ssh <you>@<backup-host> 'rm ~/db-2026-07-14-0230.sql.gz.age'
```

It prints `restore ok: … -> N clubs, M users`, or `restore check FAILED` / an error and a
non-zero exit. `restore-check.sh` restores into a scratch database it drops again afterwards;
the `down -v` removes the throwaway Postgres volume (project `rosterchief-test` — don't run
this on a machine where that name is a test instance you want to keep).

### Restoring for real

The private key has to come to the machine you restore on — bring it for the restore and
remove it afterwards. On the production server, as root (the nightly files are root's), from
the checkout:

```bash
# 1. stop the app so nothing writes during the restore
docker compose stop web

# 2. the database (as the Postgres superuser; the dump recreates everything)
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/db-2026-07-14-0230.sql.gz.age \
  | gunzip -c | docker compose exec -T db psql -U rosterchief rosterchief

# 3. uploads, if you lost them too (web is stopped, so a one-off container with its volumes)
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/media-2026-07-14-0230.tar.gz.age \
  | docker compose run --rm --no-deps -T web tar -xz -C /app
age -d -i rosterchief-backup-key.txt /var/backups/rosterchief/private-media-2026-07-14-0230.tar.gz.age \
  | docker compose run --rm --no-deps -T web tar -xz -C /app

# 4. start again, and remove the key from the server
docker compose up -d web
shred -u rosterchief-backup-key.txt
```

If the server itself is gone — or you don't trust what's on it any more — set up a new server
as in "First deploy" (with the `MFA_ENCRYPTION_KEY` from your password manager — a new key
can't read the restored TOTP secrets), then fetch one night's files from the backup server's
archive with your admin login, as in step 6, and put them on the new server:

```bash
# on your own computer: the three files with the same <date>
for f in db-2026-07-14-0230.sql.gz.age media-2026-07-14-0230.tar.gz.age private-media-2026-07-14-0230.tar.gz.age; do
  ssh <you>@<backup-host> "sudo cat /var/lib/rosterchief-backups/archive/$f" > "$f"
done
scp ./*-2026-07-14-0230.*.age <user>@<new-server>:/var/backups/rosterchief/
```

(`media-…` only exists while public uploads are on local disk.) Then restore as above. The
dump is taken with `--clean --if-exists`, so it replaces whatever is in the database; restore
as the superuser (`POSTGRES_USER`) — the application role (see "Database roles") must already
exist. After a break-in, also make a **new** upload key (step 3.3–3.6) and replace the old line
in the backup server's `authorized_keys`: the old key left with the old server.

Once uploads move to S3 (`AWS_STORAGE_BUCKET_NAME`), the bucket's own versioning is their
backup — turn versioning on when you create the bucket. `backup.sh` reads
`AWS_STORAGE_BUCKET_NAME` from `.env.production` (or its own environment) and then skips the
public-uploads archive. Private uploads always stay on local disk and
are always in the backup.

### What lives where

| Item | Where |
|---|---|
| age **public** key (`BACKUP_AGE_RECIPIENT`) | production server, `.env` in the checkout |
| age **private** key | your password manager + one offline copy. Never on a server except during a restore |
| `MFA_ENCRYPTION_KEY` | production `.env.production` + next to the age private key |
| Upload SSH key (`/root/.ssh/rosterchief_backup`) | production server only; on the backup server it may only create and write files (`sftp-server -P …`), never remove, rename or change them |
| Backups, 14 days | production server, `/var/backups/rosterchief` (owner-only: the deploy user's directory, root's nightly files); `.offsite-sent` there lists what has been uploaded |
| Uploads in transit, minutes | backup server, `~rosterchief-backup/rosterchief/incoming` |
| Backups, 60 days | backup server, `/var/lib/rosterchief-backups/archive` — root only; reached with your own admin login and `sudo`, never with the upload key |
| Uploads that weren't age files | backup server, `/var/lib/rosterchief-backups/rejected` (root only, pruned like the archive) |
| Archive job | backup server, `/usr/local/sbin/rosterchief-archive-incoming` (from `deploy/backup-server/archive-incoming.sh`), root's crontab, log in `/var/log/rosterchief-archive.log` |

## Going multi-server

Nothing in the code changes. What changes is where the services live:

| | one server | several |
|---|---|---|
| Postgres | `db` container | `DJANGO_DATABASE_URL` → your central Postgres |
| Cache / flags | `redis` container | managed Redis (or your existing one) |
| Uploads | local disk | **S3 bucket** (`AWS_STORAGE_BUCKET_NAME`) |
| Static files | WhiteNoise, in the image | unchanged — that is why WhiteNoise is there |
| Scheduled jobs | in-process scheduler, the only node | unchanged — every node runs the same in-process scheduler, but `features/scheduler.py`'s Redis leader lease makes exactly one of them the active one automatically (see "Scheduled jobs" above); no separate infra needed |
| TLS | Caddy on the box | load balancer, or Caddy on each node |

Drop `db` and `redis` from `compose.yaml`, point the URLs at the central services, and run
`web` on as many nodes as you like behind a load balancer pointed at `/healthz`.

The health check tests the database *and* a cache round trip, not just that the process is
listening — a node that cannot reach Postgres, or whose cache silently swallows writes, is
not healthy, and a load balancer must not keep feeding it traffic.

## Rollback

Releases are the unit of rollback: every `main` build is tagged `:vX.Y.Z` (and
`:main-<short-sha>`) by `.github/workflows/build-and-push.yml`, and every release has a matching
git tag. Rolling back is deploying the previous release, from your own machine:

```bash
source deploy/.env.prod.local
deploy/deploy-prod.sh --version v0.7.7
```

That checks out the old tag on the server (so the compose file and deploy scripts match too),
takes a backup, pulls `:v0.7.7`, runs `migrate` (a no-op — see below) and recreates `web`.
Prior releases are listed under the repo's GitHub Releases page, or `git tag --list 'v*'`. To go
forward again, deploy the newer tag (or plain `deploy/deploy-prod.sh` for the tip of `main`).

Don't edit `IMAGE_TAG` in the server's `.env` by hand. Every deploy (`deploy-local.sh`, and so
`deploy-prod.sh`, and `deploy-dev.sh`) passes the tag it deploys to the pull, the `migrate` and
the restart, then writes it to `.env` itself (`deploy/record-image-tag.sh`). So `.env` always
names the image `web` is running, and a by-hand `docker compose run … web` uses that same image.
A hand edit is overwritten by the next deploy.

**Migrations don't roll back with the image.** The older code's `migrate` has nothing to apply,
and it leaves the newer migrations in place — which is fine as long as they were additive.
Prefer additive migrations (add a column, deploy, backfill, then stop writing the old one) so
that yesterday's image still runs against today's schema. When one isn't:

- If the newer release's migration has a working reverse, run it **before** rolling back, with
  the newer code still deployed: `docker compose exec web python manage.py migrate <app>
  <previous migration>`. (Leaving 0.7.8 is exactly this case — see "Upgrade notes: 0.7.8".)
- Otherwise restore the backup the deploy took just before migrating (the newest
  `db-….sql.gz.age` from before that deploy — steps 1–2 of "Restoring for real" under
  "Backups"), and instead of restarting `web` there, deploy the old release as above. Anything
  written since that backup is lost.
