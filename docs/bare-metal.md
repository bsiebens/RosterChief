# Bare-metal deployment (no Docker)

Production runs directly on Rocky / AlmaLinux 9+ servers: systemd services, PostgreSQL, Redis and Caddy
installed from packages, the app in per-release directories. Three scripts in `deploy/`, run in this order:

| Script | Runs | Does |
|---|---|---|
| `prep-server.sh` | on the server, as root, once | installs and configures everything; idempotent |
| `convert.sh` | on your machine | moves an existing **Docker** install's data over, verified |
| `deploy.sh` | on your machine | deploys a release to one or more servers |

## Topology

```
              Cloudflare ──► master: Caddy (TLS, load balancer) ─┬─► master web :8000
                             PostgreSQL · Redis                   └─► web node(s) :8000
                             beat · worker · web                        worker · web
```

- **master** (`--master`): Caddy, PostgreSQL, Redis, gunicorn, Celery worker **and beat**.
- **web node** (`--web-node`): gunicorn + Celery worker only; talks to the master's PostgreSQL and Redis.
  `--skip-caddy/--skip-postgres/--skip-redis/--skip-beat` fine-tune a role.
- Exactly one beat fleet-wide (a second would queue every job twice).
- **Uploads are node-local** (`media/`, `private_media/`). With more than one web node, set
  `AWS_STORAGE_BUCKET_NAME` for public media; private uploads still need shared storage — run one node serving
  until then.

## 1. Prepare the servers

```bash
# master (one server)
export CLOUDFLARE_API_TOKEN=...        # DNS:Edit on the zone: the wildcard certificate needs DNS-01
./prep-server.sh --master --base-domain rosterchief.app --acme-email ops@rosterchief.app \
    --backup-age-recipient age1... --backup-remote offsite:rosterchief/incoming

# master with extra web nodes on a private network
./prep-server.sh --master ... --private-ip 10.0.0.1 --web-node-ip 10.0.0.2

# each web node: copy the master's env file first
scp master:/etc/rosterchief/rosterchief.env .
./prep-server.sh --web-node --master-ip 10.0.0.1 --env-file ./rosterchief.env
```

It generates the secrets in `/etc/rosterchief/rosterchief.env` (back up `MFA_ENCRYPTION_KEY` separately — see
"Two-factor secrets"); fill in email settings and the rest of `.env.production.example` by hand. It also sets up
**nightly backups** (systemd timer, `rosterchief-backup.timer`) on any node running PostgreSQL — see "Backups".

Redis is both cache and Celery broker, so it is configured `volatile-lru` + `appendonly yes` + 256 MB, never the
old cache-only flags (see "Scheduled jobs").

## 2. Moving from Docker (once)

```bash
deploy/convert.sh --source deploy@old-host --source-dir /home/deploy/RosterChief --target root@new-host
```

Puts the old platform in maintenance mode, dumps the database and both upload volumes to a local directory,
restores them on the new server in **one transaction** (refusing if the target database isn't empty), then
compares the **row count of every table** on both sides — any difference fails the run. It also carries over
`DJANGO_SECRET_KEY` and `MFA_ENCRYPTION_KEY`. It does not start the app. Rehearse with `--no-freeze` first. The
old server stays untouched (still in maintenance) until you point DNS away, so rolling back is a DNS change.

## 3. Deploy

```bash
deploy/deploy.sh --master prod1 --web-node prod2 --version v1.2.0   # --version omitted = latest release
```

Downloads the release tarball CI attached to the GitHub release (checksum-verified), unpacks it on every server,
runs `uv sync`, then: maintenance on → backup → `migrate` → `check --deploy` → `install_tasks` → switch the
`current` symlink and restart (master first, each node waiting for `/healthz`) → verify the public URL → ask
whether to reopen. A failure leaves maintenance **on**. Roll back by deploying an older `--version` (migrations
are not reversed; see "Rollback"). Config for repeated use: `deploy/.env.deploy.example`.

## Layout on a server

```
/opt/rosterchief/releases/vX.Y.Z/   code + .venv (root-owned, read-only to the app)
/opt/rosterchief/current            symlink to the live release
/opt/rosterchief/shared/{media,private_media}   uploads, linked into every release
/etc/rosterchief/{rosterchief.env,node.env}     secrets / per-node settings
rosterchief-web | -worker | -beat | -backup     systemd units (deploy/systemd/)
rosterchief-manage <args>           manage.py with the app's environment, as the app user
```

Logs: `journalctl -u rosterchief-web -u rosterchief-worker -u rosterchief-beat`.

## Backups

`prep-server.sh` installs the nightly timer; `deploy/backup.sh` (`BARE_METAL=1`) does the work: dump + uploads,
age-encrypted, 14 days local, copied offsite append-only. Create the age key and the backup server as described
in DEPLOYMENT.md "Backups" (the Docker-specific commands there are replaced by the above). Prove a backup
restores monthly: `BARE_METAL=1 deploy/restore-check.sh`.
