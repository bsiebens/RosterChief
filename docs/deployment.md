# Deploying RosterChief: first server and updates

This is the checklist. The command reference and the details of how a host installs a release are in [`deploy/README.md`](../deploy/README.md).

> **Status.** The tooling (`deploy/rc`, `deploy/host/*`, the GitHub workflows) is linted and dry-run tested, but it has not yet been run against a real server or a real GitHub release. Do the first run on **staging** and expect to fix small things. The Caddyfiles in particular have not been validated by Caddy itself (see "First deploy" step 4).

## What you need before the first server

**Accounts and keys**

| Need | For | Notes |
| --- | --- | --- |
| A domain on **Cloudflare** | DNS and the wildcard certificate | The apex and `*.domain` must point at the server. `control.<domain>` is reserved for the Control Panel. |
| Cloudflare **API token** | Caddy's certificate (DNS challenge) | Permissions *Zone:Zone:Read* and *Zone:DNS:Edit*, restricted to that one zone. |
| **GitHub** repository with Actions enabled | Building releases | The workflows are in `.github/workflows/`. |
| GitHub **read-only token** | Servers downloading releases | Fine-grained, *Contents: read* on this repository only. |
| **Resend** account (or another mail provider) | Confirmation, password reset, invoices, notifications | See "Email" below. Verify your sending domain first. |
| **AWS** account | The servers | Amazon Linux 2023 instances. The AWS CLI only if you use SSM instead of SSH. |

**Servers**

- Amazon Linux 2023, 64-bit. For one box: 2 vCPU / 4 GB RAM is a sensible start. Attach a persistent EBS volume.
- Security group: ports 80 and 443 open (see the Cloudflare note below). Keep PostgreSQL, Valkey, Gunicorn and the SvelteKit ports private. Open SSH only to your own address, or use SSM and open nothing.
- For a split setup (separate frontend and backend), put both in the same VPC. The backend listens on its **private address, port 8001**, and only the frontend host(s) may reach it.
- SSH access needs key login and passwordless `sudo` for the operator user (`ec2-user` by default).

**On your own machine**

`bash`, `git`, `uv`, the GitHub CLI (`gh`), and `ssh` (or the `aws` CLI for SSM hosts).

## One-time setup

1. **Environment files.** In `deploy/environments/` copy `staging.env.example` to `staging.env` (and `production.env.example` to `production.env`). Fill in the domain, owner email, `GITHUB_REPO`, whether Cloudflare proxies the domain (`CLOUDFLARE_PROXIED`), and the host list. They are git-ignored.
2. **Secrets.** Copy `secrets.example` to `staging.secrets`, then `chmod 600` it, and fill in `CLOUDFLARE_API_TOKEN` and `GITHUB_TOKEN`. `rc` refuses to read it if others can.
3. **Cloudflare.**
   - Add `A` records for `domain` and `*.domain` pointing at the frontend/full server's public address.
   - If you proxy (orange cloud): set **SSL/TLS to Full (strict)**, and restrict ports 80 and 443 in the security group to [Cloudflare's ranges](https://www.cloudflare.com/ips/). Check that your plan proxies wildcard records. Cloudflare limits uploads (100 MB on Free and Pro).
   - If you do not proxy: leave the records as DNS only and open 80/443 to everyone.
4. **Email** (see the section below): verify the sending domain, create the API key.

## First deploy

1. **Cut the first release.** From a clean, pushed `development` branch:
   ```bash
   deploy/rc release 1.0.0
   gh run watch          # CI builds and publishes the GitHub Release
   ```
   This merges `development` into `main`, sets the version, tags `v1.0.0` and pushes. The release holds four files: backend, frontend, host scripts and `SHA256SUMS`.
2. **Provision the server** (installs packages, PostgreSQL, Valkey, Caddy with the Cloudflare module, users, directories, systemd units):
   ```bash
   deploy/rc provision staging --host app1
   ```
   Over SSH this also sends the two tokens. Over SSM it does not (SSM keeps command text in its history): put `CLOUDFLARE_API_TOKEN` in `/etc/rosterchief/caddy.env` and `GITHUB_TOKEN` in `/etc/rosterchief/deploy.env` yourself, mode 600.
3. **Configure the app** on the server: `sudoedit /etc/rosterchief/app.env`. Set the email option (below). Optionally the VAPID keys for web push (`uv run python manage.py generate_vapid_keys --email you@example.com`). A secret key is already generated. Frontend-only hosts have no `app.env`.
4. **Deploy:**
   ```bash
   deploy/rc deploy staging v1.0.0
   deploy/rc status staging
   ```
   The deploy validates the Caddyfile before changing anything, so a syntax error stops it early. Two Caddy behaviours are worth checking by hand on the first run: that the real client IP reaches the apps (`X-Forwarded-For` through Cloudflare), and that `rosterchief-health --tls` passes, including its forged-header check.
5. **First login.** On the backend (or full) host, set the platform owner's password, using the owner email from the environment file:
   ```bash
   sudo systemd-run --wait --pty --collect \
     -p User=rosterchief -p WorkingDirectory=/opt/rosterchief/current \
     -p EnvironmentFile=/etc/rosterchief/app.env \
     /opt/rosterchief/current/.venv/bin/python manage.py changepassword owner@example.com
   ```
   Then open `https://control.<domain>`, sign in, enrol MFA, and add or restore a club. Check a club's own host, and a legacy public API URL if you migrated one.
6. **Send a test email** (below), and check `sudo rosterchief-health --tls`.
7. **Production** is the same, once staging is happy: `provision production …`, then `deploy/rc deploy production v1.0.0`. Production refuses a version that has not run on every staging host (override once with `--force`).

### Split frontend and backend

List one backend (with `private_ip=`) and one or more frontends in the environment file, set `TRUSTED_CIDR` to the VPC range, and provision each host. `rc` derives the frontend's backend address (`http://<private_ip>:8001`) itself. Migrations run only on the backend, which is always deployed first. PostgreSQL, Valkey, media and private media live on the backend host.

## Email

Every email goes through Django's mailer, chosen in `/etc/rosterchief/app.env`. **The previous version used Resend**, and both of its ways work here:

| Option | Settings in `app.env` |
| --- | --- |
| **Resend HTTP API** (same as before) | `RESEND_API_KEY=re_...` and `DJANGO_FROM_EMAIL=RosterChief <accounts@yourdomain>` |
| Resend's SMTP relay | `DJANGO_SMTP_HOST=smtp.resend.com`, `DJANGO_SMTP_PORT=587`, `DJANGO_SMTP_USER=resend`, `DJANGO_SMTP_PASSWORD=<the API key>`, `DJANGO_FROM_EMAIL=…` |
| Any other SMTP (Amazon SES, …) | the same `DJANGO_SMTP_*` settings |

Set one, not both: the app refuses to start if `RESEND_API_KEY` and `DJANGO_SMTP_HOST` are both set. With neither, mail is printed to the log and never sent, so nobody can confirm an address.

The HTTP API option carries the plain text, the HTML version, cc/bcc/reply-to and attachments (invoices are PDFs). It was ported from the previous version and tested with a mocked Resend; it has not yet sent a real message from this codebase.

**Domain.** In Resend, add the sending domain and publish the DNS records it gives you (SPF/DKIM) in Cloudflare, as **DNS only** records (not proxied). `DJANGO_FROM_EMAIL` must be an address on that verified domain.

**Test it** after changing `app.env` (restart the web and worker services so they pick it up):

```bash
sudo systemctl restart rosterchief-web rosterchief-worker
sudo systemd-run --wait --pty --collect \
  -p User=rosterchief -p WorkingDirectory=/opt/rosterchief/current \
  -p EnvironmentFile=/etc/rosterchief/app.env \
  /opt/rosterchief/current/.venv/bin/python manage.py sendtestemail you@example.com
```

Then trigger a real one: a password reset from the sign-in page. The platform can pause automated email in the Control Panel; password resets and invitations are exempt.

## Updating later

```bash
deploy/rc release 1.1.0            # from a clean, pushed development branch
gh run watch                       # wait for the GitHub Release to have its files
deploy/rc deploy staging v1.1.0
deploy/rc status staging           # check the versions and health
deploy/rc deploy production v1.1.0
```

Each host verifies checksums, unpacks the new release, takes a PostgreSQL checkpoint (backends), runs migrations, switches over and health-checks, then reverts on its own if the health check fails. Expect a short outage on the backend while services restart. Frontend-only hosts restart in seconds.

Things that can need a manual step on a given release: new variables in `app.env` (the deploy does not edit it, so read the release notes), a migration that cannot run on the old code (then rolling back means restoring the checkpoint), and Caddyfile changes (these ship with the release and are validated before use).

## Rolling back

```bash
deploy/rc rollback production
deploy/rc status production
```

This switches code only. If the release you left ran migrations the previous code cannot work with, restore the pre-migration checkpoint into a new database (see "Rollback and recovery" in `deploy/README.md`).

## Backups

Not automated yet. Back up `/var/lib/rosterchief/checkpoints` (a `pg_dump` before every release), `/var/lib/rosterchief/media` and `/var/lib/rosterchief/private_media` off the host, and snapshot the EBS volume. Test a restore once.

## When something is wrong

| Symptom | First look |
| --- | --- |
| A deploy fails | The output of `rc deploy`; on the host `journalctl -u rosterchief-web -u rosterchief-management -u rosterchief-controlpanel --since today` |
| `rc status` says health is failing | `sudo rosterchief-health --tls` on the host shows which unit or probe fails |
| No certificate | `journalctl -u rosterchief-caddy`; usually the Cloudflare token's permissions or the zone |
| Wrong client IP in logs | `CLOUDFLARE_PROXIED` does not match the real DNS setting. Fix with `rc proxy ENV on` or `off` |
| Email not arriving | `sendtestemail` above; check the domain is verified, the from address is on it, and the provider's dashboard |
