# Deploying RosterChief

One server today, several later, with no code changes in between — only environment
variables. This document is the runbook and, more usefully, the list of things that are
specific to *this* app and will bite you if you treat it as a generic Django deploy.

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
a feature that "sometimes doesn't turn on". A shared cache is the fix.

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
# 1. Configure
cp .env.compose.example .env                 # read by docker compose
cp .env.production.example .env.production   # read by Django
python -c "import secrets; print(secrets.token_urlsafe(64))"   # -> DJANGO_SECRET_KEY

# 2. Build and start
docker compose build
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
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up -d --no-deps web
```

## Scheduled jobs

Two commands need to run on a schedule. Put them on the **host**, not in a container, and on
**exactly one node** when you have several — three nodes archiving the same club is three
emails to the same club.

```cron
# Bill: archive clubs unpaid past their grace period.
# Run it WITHOUT --commit for the first week and read the output. The flag exists because
# this switches off paying customers: a bad clock or a bad cron should cost you an email,
# not a morning of angry clubs.
0 6 * * *  cd /srv/rosterchief && docker compose run --rm web python manage.py archive_overdue_clubs --commit

# Events: extend recurring series so the calendar never runs dry.
0 3 * * *  cd /srv/rosterchief && docker compose run --rm web python manage.py extend_event_series
```

## Maintenance mode

Control panel → **Features → Maintenance mode**. While it is on:

- every **club subdomain** serves a 503 maintenance page, in that club's own colours;
- the **control panel and the sign-in screens stay open**, because closing them would leave
  you with no way to turn it back off;
- `/healthz` keeps answering on every host, or the load balancer would take the node out of
  rotation and the control panel with it;
- the **scheduled jobs stand down** — `archive_overdue_clubs`, `extend_event_series` and
  `import_members_csv` refuse to run.

`migrate` and `collectstatic` are deliberately **not** blocked. Maintenance is usually
declared *in order* to run them, and a guard that stopped them would mean turning the mode
off to do the work you turned it on for.

The scheduled jobs exit **non-zero** while the platform is closed, so cron will mail you.
That is intended: a job that silently skips itself is how a month of billing goes missing. If
you genuinely mean to run one during a window, pass `--ignore-maintenance`.

So a migration-heavy deploy looks like:

```bash
# 1. Close the platform in the control panel (or from a shell):
docker compose run --rm web python manage.py shell -c \
  "from features.models import Maintenance; Maintenance.start(message='Upgrading. Back by 21:00.')"

# 2. Do the work — migrate is not blocked.
docker compose build
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
}

# --- whatever the box already serves --------------------------------------
existing-thing.example.com {
	reverse_proxy 127.0.0.1:3000
}

# --- RosterChief test instance --------------------------------------------
# The bare host AND the wildcard, on one certificate.
test.rosterchief.app, *.test.rosterchief.app {
	encode zstd gzip

	reverse_proxy 127.0.0.1:8001 {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Real-IP {remote_host}
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
   without it Django believes the request behind the proxy is plain HTTP.

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
deploy/deploy-dev.sh            # deploy the current branch
BRANCH=main deploy/deploy-dev.sh
deploy/deploy-dev.sh --push     # push the branch first, then deploy
```

It runs from your machine and does the work on the server in one SSH session: fetch the pushed
branch (a hard reset to `origin/<branch>`, since a deploy target only receives deploys), build
the image, run migrations *explicitly*, restart only `web`, and wait for `/healthz`.

It refuses to deploy a branch whose local commits are not pushed — the server pulls from git,
so unpushed work would ship stale code silently. Override the host, user, directory or branch
with the `SSH_HOST` / `SSH_USER` / `REMOTE_DIR` / `BRANCH` environment variables.

First-time setup on the server, once:

```bash
git clone git@git.siebens.org:bernard/RosterChief.git /home/bernard/RosterChief
cd /home/bernard/RosterChief
cp .env.compose.example .env             # fill in POSTGRES_PASSWORD etc.
cp .env.production.example .env.production
# then add the reverse_proxy site block to the host's Caddy (see above)
```

## Automated backups

`deploy/backup.sh` dumps the database, tars the uploads while they are still on local disk,
prunes anything older than `KEEP_DAYS`, and — if you set `BACKUP_REMOTE` — copies the lot off
the box with rclone.

```bash
deploy/backup.sh /var/backups/rosterchief
```

It writes to a `.part` file and only moves it into place once `gzip -t` says the archive is
readable and non-empty. A truncated dump that *looks* like a backup is the failure mode worth
engineering against, because you only discover it on the day you need it.

Schedule it as root on the host (single server; on several, run it on the database node):

```cron
# Nightly at 02:30, before the billing and event jobs.
30 2 * * *  cd /srv/rosterchief && BACKUP_REMOTE=b2:rosterchief-backups KEEP_DAYS=14 deploy/backup.sh /var/backups/rosterchief

# Weekly restore rehearsal into a throwaway database. This is the only line here that proves
# the others work.
0 4 * * 0   cd /srv/rosterchief && deploy/restore-check.sh
```

Cron mails you on non-zero exit, and the script uses `set -Eeuo pipefail` so it *does* exit
non-zero. A backup script that fails quietly is worse than none, because you will believe you
have backups.

**Offsite matters more than frequency.** A dump sitting on the same disk as the database
survives a bad migration but not the server. `BACKUP_REMOTE` takes any rclone remote (S3,
Backblaze, a second box).

**Once uploads move to S3** (`AWS_STORAGE_BUCKET_NAME`), the script skips the media tarball:
the bucket's own versioning is the backup. Turn versioning on when you create it.

### Restoring

```bash
gunzip -c /var/backups/rosterchief/db-2026-07-14-0230.sql.gz \
  | docker compose exec -T db psql -U rosterchief rosterchief
```

The dump is taken with `--clean --if-exists`, so it drops and recreates rather than colliding
with what is there. Rehearse it once, now, against a scratch database — not the first time you
need it.

## Backups (manual)

Two things carry state: Postgres and the uploads.

```bash
# Database
docker compose exec -T db pg_dump -U rosterchief rosterchief | gzip > rosterchief-$(date +%F).sql.gz

# Uploads — until they are on S3, in which case the bucket's own versioning is the backup.
docker compose cp web:/app/media ./media-backup
```

Restore is `gunzip -c dump.sql.gz | docker compose exec -T db psql -U rosterchief rosterchief`.
Test it once, now, rather than the first time you need it.

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

Measured, running this app under gunicorn with `DEBUG=False`:

| | memory |
|---|---|
| gunicorn master + 3 workers | **~270 MB** (~54 MB per worker) |
| PostgreSQL (default `shared_buffers`) | ~200–400 MB |
| Redis (cache only) | < 50 MB |
| Caddy | ~30 MB |
| OS + Docker daemon | ~400 MB |
| **steady state** | **~1.0–1.2 GB** |

2 GB would run it. 4 GB is the recommendation for three reasons, all of which are the kind of
thing that bites at the worst moment:

1. **`docker compose build` is the memory spike, not serving.** npm, uv and `collectstatic`
   together will OOM a 2 GB box that is also running Postgres. Either take the 4 GB, or build
   the image elsewhere and pull it.
2. **Rendering an invoice loads WeasyPrint.** It is imported lazily (which is why the workers
   measure 54 MB and not 150), so pango and its fonts land in whichever worker renders a PDF —
   expect that worker to grow by ~50–100 MB the first time someone downloads an invoice.
3. **Headroom is Postgres's page cache.** With 200 MB of data and 4 GB of RAM, the entire
   database lives in cache and the disk is never touched for reads.

### Disk

| | |
|---|---|
| Docker images (app ~1 GB with pango, postgres, redis, caddy) | ~1.5 GB |
| Build cache | 2–4 GB |
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
| Cron | host crontab | one node only |
| TLS | Caddy on the box | load balancer, or Caddy on each node |

Drop `db` and `redis` from `compose.yaml`, point the URLs at the central services, and run
`web` on as many nodes as you like behind a load balancer pointed at `/healthz`.

The health check tests the database *and* a cache round trip, not just that the process is
listening — a node that cannot reach Postgres, or whose cache silently swallows writes, is
not healthy, and a load balancer must not keep feeding it traffic.

## Rollback

Images are the unit of rollback. Tag on build, keep the last few, and:

```bash
docker compose up -d --no-deps web   # with the previous image tag
```

Migrations are the exception: they don't roll back with the image. Prefer additive migrations
(add a column, deploy, backfill, then stop writing the old one) so that yesterday's image
still runs against today's schema.
