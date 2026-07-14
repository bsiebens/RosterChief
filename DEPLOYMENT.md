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
