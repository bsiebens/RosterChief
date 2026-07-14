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
Club logos go to `MEDIA_ROOT` on local disk. On one box that is fine. On two, a logo
uploaded to node A is a 404 on node B. Setting `AWS_STORAGE_BUCKET_NAME` switches the
default storage to S3 — do it *before* you scale, not during.

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

## Backups

Two things carry state: Postgres and the uploads.

```bash
# Database
docker compose exec -T db pg_dump -U rosterchief rosterchief | gzip > rosterchief-$(date +%F).sql.gz

# Uploads — until they are on S3, in which case the bucket's own versioning is the backup.
docker compose cp web:/app/media ./media-backup
```

Restore is `gunzip -c dump.sql.gz | docker compose exec -T db psql -U rosterchief rosterchief`.
Test it once, now, rather than the first time you need it.

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
