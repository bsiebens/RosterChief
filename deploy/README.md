# Releasing and deploying RosterChief

New to this? Start with [`docs/deployment.md`](../docs/deployment.md): the prerequisites and the first-server and update checklist. This file is the command reference.

Everything is driven by one command, `deploy/rc`, run from a checkout on your own machine.

```
development ──rc release 1.4.0──▶ main + tag v1.4.0 ──GitHub Actions──▶ GitHub Release (artifacts)
                                                                         │
            rc deploy staging v1.4.0 ◀───────────────────────────────────┤  hosts download + verify + install
            rc deploy production v1.4.0  (only after staging)            ┘
```

| Command | What it does |
| --- | --- |
| `rc release 1.4.0` | Checks `development` is clean and pushed, lints, merges it into `main`, sets the version, tags `v1.4.0`, pushes. CI builds and publishes the artifacts. Then fast-forwards `development`. |
| `rc deploy ENV v1.4.0` | Installs the release on every host of the environment, backends first. Stops at the first failure. |
| `rc rollback ENV` | Switches each host back to its previous completed release (frontends first). |
| `rc status ENV` | Role, running version and health of every host. |
| `rc provision ENV --host NAME` | Prepares a new server for the role listed in the environment file. |
| `rc proxy ENV on\|off` | Switches whether Caddy trusts Cloudflare's `CF-Connecting-IP` header. |

Add `--dry-run` to any command to print what would run without touching anything, `--host NAME` / `--role …` to narrow it, and `--yes` to skip prompts. A production environment (any name containing `prod`) asks you to type its name.

## One-time setup

1. **Environment files.** Copy `deploy/environments/staging.env.example` to `staging.env` (and `production.env.example` likewise). They list the servers and are git-ignored. Copy `secrets.example` to `staging.secrets`, `chmod 600`, and fill in:
   - `CLOUDFLARE_API_TOKEN`: scoped to the one zone with *Zone:Read* and *DNS:Edit*. Caddy uses it for the wildcard certificate (DNS challenge).
   - `GITHUB_TOKEN`: a fine-grained, read-only *Contents* token for this repository. Hosts use it to download releases.
2. **GitHub.** The workflows in `.github/workflows/` run on the repository: `ci.yml` on pull requests and pushes, `release.yml` when a `v*` tag is pushed.
3. **Operator access.** SSH hosts need key login and passwordless `sudo`. SSM hosts need the AWS CLI configured (and the instance in Systems Manager).

## Topologies

| Role | Runs | Public? |
| --- | --- | --- |
| `full` | PostgreSQL, Valkey, Django, Celery worker and beat, both SvelteKit apps, Caddy | Yes (TLS) |
| `backend` | PostgreSQL, Valkey, Django, Celery, media, a Caddy listener on the private address only (port 8001) | No |
| `frontend` | Both SvelteKit apps, Caddy with the wildcard certificate | Yes (TLS) |

A split environment lists one backend (with `private_ip=`) and one or more frontends. Frontends reach the backend at `http://<private_ip>:8001` and forward everything Django owns to it, including `/static` and `/media`. Migrations run on the backend host only, and the backend is always deployed before the frontends. Postgres, Valkey, media and private media stay on the backend host.

## Provisioning a new server

1. Launch an Amazon Linux 2023 instance. Put the backend and frontends in the same VPC.
2. Add it to the environment file's `HOSTS`, then:
   ```bash
   deploy/rc provision staging --host web1
   ```
   `provision` ships the host scripts and runs `bootstrap.sh` with the right role. It installs packages, creates the users, database and directories, installs a Caddy build with the Cloudflare DNS module, and writes `/etc/rosterchief/*.env`. Over SSH it also sends the two tokens. Over SSM it does **not** (SSM keeps command text in its history), so put them in `/etc/rosterchief/caddy.env` and `/etc/rosterchief/deploy.env` yourself, mode 600.
3. Do what it prints: DNS records for the apex and wildcard, the security group rules, and for backend/full hosts SMTP and VAPID settings in `/etc/rosterchief/app.env`.
4. Deploy: `deploy/rc deploy staging v1.4.0 --host web1`.
5. First install only: set the platform owner's password (see "First release" below).

## Cloudflare proxy switch

`CLOUDFLARE_PROXIED` in the environment file (default `true`) decides what Caddy trusts:

- **true**: Caddy trusts `CF-Connecting-IP` only when the TCP peer is inside Cloudflare's published ranges. `/etc/rosterchief/cloudflare-ips.caddy` holds that list; a weekly timer refreshes it and reloads Caddy (and keeps the old list if the new one looks wrong). Caddy then passes the real client address to the SvelteKit apps as `X-Forwarded-For`.
- **false**: nothing forwarded is trusted; the TCP peer address is the client.

Switch at any time without a release: `deploy/rc proxy production off`. It also updates the environment file. Change the DNS record's proxy status in Cloudflare and the security group yourself.

When proxied:

- Set Cloudflare **SSL/TLS to Full (strict)**. Flexible mode sends plain HTTP to the origin and loops.
- Restrict the security group's ports 80 and 443 to [Cloudflare's ranges](https://www.cloudflare.com/ips/). This keeps the origin from being reached directly and from being handed forged headers.
- Cloudflare limits request bodies (100 MB on Free and Pro) and times out slow origin responses after about 100 seconds. The control panel accepts 520 MB uploads, which only work if the plan allows it or the upload goes around the proxy.
- Confirm your plan proxies wildcard records. If it does not, club subdomains are DNS-only.

Every health check (`rosterchief-health --tls`) sends a forged `CF-Connecting-IP` from the loopback address and fails if Caddy believes it.

## Releasing

```bash
deploy/rc release 1.4.0          # from an up-to-date, clean development branch
gh run watch                     # CI builds the artifacts and publishes the GitHub Release
deploy/rc deploy staging v1.4.0
deploy/rc status staging
deploy/rc deploy production v1.4.0
```

A release has four files: `rosterchief-vX-backend.tar.gz` (source and `uv.lock`), `…-frontend.tar.gz` (built SvelteKit apps and production `node_modules`), `…-host.tar.gz` (these scripts, systemd units, Caddyfiles) and `SHA256SUMS`. Servers verify every checksum before unpacking and build nothing: backends run `uv sync --locked`, frontends are ready to start.

`rc deploy production` refuses a version that has not run on every host of `PROMOTE_FROM` (staging). `--force` overrides it.

### What a host does for a release

1. Downloads and verifies the artifacts, then re-runs itself from the new release's `deploy.sh` if that changed.
2. Validates the new Caddyfile, then installs scripts, units and the Caddyfile.
3. Unpacks into `/opt/rosterchief/releases/<version>.<random>`. Backends install the locked Python environment, run Django's deploy checks and `collectstatic`.
4. Stops the application services, then on backends takes a PostgreSQL checkpoint (`/var/lib/rosterchief/checkpoints`) and runs `migrate_schemas`. This is a short maintenance window.
5. Switches the `current` symlink, starts the services and waits for the health checks. On failure it stops, restores the previous symlink and exits non-zero.
6. Marks the release complete, records the version in `/var/lib/rosterchief/deployed-versions`, reloads Caddy and keeps the five newest completed releases.

Directly on a host (for recovery): `sudo rosterchief-deploy release vX.Y.Z [--from-dir DIR]`, `sudo rosterchief-deploy rollback [DIR]`, `sudo rosterchief-deploy status`.

## Rollback and recovery

```bash
deploy/rc rollback production
deploy/rc status production
```

A rollback switches code only. If the release you are leaving ran migrations the previous code cannot work with, keep the services stopped and restore the pre-migration checkpoint into a **new** database so the failed database stays available for inspection. Set `DJANGO_DATABASE_URL=postgresql:///rosterchief_recovery` in `app.env` and `ROSTERCHIEF_DATABASE_NAME=rosterchief_recovery` in `site.env` before activating the previous release. A database restore returns data to the checkpoint time.

```bash
sudo systemctl stop rosterchief-web rosterchief-management rosterchief-controlpanel rosterchief-beat rosterchief-worker
sudo -u postgres createdb -O rosterchief rosterchief_recovery
sudo sh -c 'runuser -u postgres -- pg_restore --exit-on-error --no-owner --role=rosterchief --dbname=rosterchief_recovery < "$1"' \
  sh "/var/lib/rosterchief/checkpoints/CHECKPOINT_FILENAME"
```

Archive the checkpoints and the media directories off the host (EBS snapshots or another backup). Migrations that only add things keep the previous release usable; destructive ones need the restore.

## First release on a new environment

After the first `rc deploy` to a host that carries the backend, set the platform owner's password (use the owner email from the environment file) and enable MFA:

```bash
sudo systemd-run --wait --pty --collect \
  -p User=rosterchief -p WorkingDirectory=/opt/rosterchief/current \
  -p EnvironmentFile=/etc/rosterchief/app.env \
  /opt/rosterchief/current/.venv/bin/python manage.py changepassword owner@example.com
sudo rosterchief-health --tls
```

Then sign in at `https://control.<domain>`, enroll MFA, and add or restore a club. Check that a club host and a legacy public API URL work.

Configure SMTP in `/etc/rosterchief/app.env` before inviting users. For Amazon SES, create SMTP credentials and verify the sender in the chosen region. The app uses the console mail backend until SMTP is configured.

## Monitoring

A systemd timer runs `rosterchief-health --tls` every minute: unit states, PostgreSQL and Valkey (backend/full), both frontend login routes through Caddy, and the forged-header check. Feed timer failures and restarts into your host monitoring, and use an external HTTPS probe on `/healthz` to cover DNS, Cloudflare and the certificate.

```bash
systemctl list-timers rosterchief-health.timer rosterchief-cloudflare-ips.timer
journalctl -u rosterchief-health -u rosterchief-caddy -u rosterchief-web -u rosterchief-worker -u rosterchief-beat --since today
journalctl -u rosterchief-management -u rosterchief-controlpanel --since today
```

Review Celery run history in the Control Panel for task failures.

## Moving an existing host from the old Route 53 setup

Hosts bootstrapped before this tooling used Caddy with the Route 53 DNS module and have no role in `site.env`. To adopt them: put the host in an environment file as `full`, create a Cloudflare API token, then run `rc provision ENV --host NAME`. It replaces the Caddy build (the old one lacks the Cloudflare module), keeps the database, secrets and `app.env`, and the next `rc deploy` brings it onto the new layout. Move the zone's DNS to Cloudflare first, since Caddy can only answer ACME challenges in the zone it manages.

## Installed club app and browser push

Open the club URL over HTTPS. Members can install it from **Me → App & notifications**. On iPhone and iPad, use Safari's **Share → Add to Home Screen**, then open the installed app before enabling push.

Generate a key pair with `uv run python manage.py generate_vapid_keys --email you@example.com` (it prints the three lines and writes nothing). Set `DJANGO_VAPID_PUBLIC_KEY`, `DJANGO_VAPID_PRIVATE_KEY` and `DJANGO_VAPID_ADMIN_EMAIL` in `/etc/rosterchief/app.env` and restart the Django web and worker services. Use a stable key pair because existing browser subscriptions are tied to the public key. Enable Web push in **Control panel → Push**, then subscribe from the installed app and send a test notification to that user.

Use the member's notification bell to distinguish a missing invitation from a push delivery problem. A future event created with **Tell the invited people** sends an in-app message to the invited members with an account and to the parents or guardians of invited children. The account creating the event is skipped, including its copies for children. Other linked guardians still receive their own copies.

Officiating invitations use a separate job. After applying tenant migrations, check that `rosterchief-worker` and `rosterchief-beat` are running. **Control panel → Jobs** should show `events-notify-officiating-opportunities` running every ten minutes and `notifications-process-outbox` running every minute. The officiating job covers eligible games starting within 30 days and can notify the creator if they are eligible themselves or are a guardian of an eligible child. The bell does not require a browser subscription; device delivery also follows the recipient's push preferences and quiet hours.
