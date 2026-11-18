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
- Every host gets a 1 GB swap file when `rc provision` runs (Amazon Linux has none), so a memory spike slows the host down instead of freezing it. `--swap-mb` on `bootstrap.sh` changes the size; 0 skips it.
- Sizing profiles, set as `PROFILE=` in the environment file, applied by `rc provision` and a deploy (the unit drop-ins are in `deploy/host/profiles/`):

  | Profile | For | What it does |
  |---|---|---|
  | `standard` (default) | 4 GB and up | No limits. |
  | `medium` | 2 GB, production | `MemoryHigh`/`MemoryMax` on the web (650 MB), worker (450 MB, children recycled past 200 MB), beat and both Node apps (280 MB, 192 MB heap), so a runaway service is restarted instead of starving the host. PostgreSQL sized to 50 connections, Valkey capped at 128 MB without eviction (it holds the Celery queue). `sshd` and PostgreSQL are protected from the OOM killer. |
  | `small` | 1 GB, staging for one person | The same idea, tighter, and no Celery worker or beat: tasks run inside the web request (scheduled jobs do not run), one gunicorn worker. Do not use it for production. |

  The caps are estimates. If a unit keeps hitting its `MemoryMax` (`systemctl status rosterchief-web` shows it), raise that unit's value in `deploy/host/profiles/<profile>/`.
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
   gh run watch          # CI checks, tests, then builds and publishes the GitHub Release
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

### Staging without a domain (reached by IP address)

For a throwaway staging host you can skip the domain, Cloudflare and the API token. In `staging.env` comment out `BASE_DOMAIN` and set `ACCESS=ip`. Everything else stays the same (`provision`, `deploy`, `status`).

- The base domain becomes **`<ip>.sslip.io`**, for example `203-0-113-10.sslip.io` for 203.0.113.10. [sslip.io](https://sslip.io) answers every name under it with that address, so the control panel is `https://control.203-0-113-10.sslip.io` and a club called `sharks` is `https://sharks.203-0-113-10.sslip.io`. Nothing is configured in DNS. The club slug-to-subdomain routing the app relies on keeps working.
- The IP is read from an ssh target (`ec2-user@203.0.113.10`). For an SSM host add `public_ip=203.0.113.10` to its options.
- Caddy signs the certificates with its own certificate authority, so the browser warns once for each name. Accept it. Passkeys and secure cookies still work, but the page is not "trusted", and installing the app to a phone's home screen may be refused. Use a real domain when that matters.
- There is no Cloudflare proxy, so `rc proxy` is refused. Open ports 80 and 443 to the people who test it (your own address is enough).
- Use a fixed address (an Elastic IP), or the names change when the instance restarts. A new address is a new base domain: provision again and the clubs have to be made again.
- It depends on sslip.io being up. For staging that is an acceptable risk. Production should keep a real domain.
- Email: the Resend sending domain cannot be an sslip.io name. Leave email unset on such a staging host and set `DJANGO_MAIL_TO_LOG=True` in `app.env` (messages are written to the log; without that flag the deploy check fails with `mail.E001`), or point the sender at a domain you do own.

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

## Backups and the offsite copy (reusing the old setup)

The old version took an encrypted backup every night and copied it to a **second server** over SFTP, in a way that a break-in on the production server cannot wipe. The full write-up is `DEPLOYMENT.md`, section "Backups", in the old repository (the checkout next to this one), with the scripts `deploy/backup.sh`, `deploy/restore-check.sh` and `deploy/backup-server/archive-incoming.sh`. Most of it carries over. Only the part that runs on the production server has to change.

**How the old setup works, in short.** Each night `backup.sh` dumps the database and tars the uploads, encrypts each file with [age](https://age-encryption.org) to a **public** key (the server cannot decrypt its own backups), keeps 14 days locally, and uploads new `*.age` files with `rclone` to the backup server. The upload key can only create files: its `sftp-server` refuses remove, rename and setstat. A root job on the backup server moves finished uploads every 15 minutes into a root-only archive and prunes it after 60 days. Restores use your own admin login on the backup server and the age **private** key, which never lives on a server.

### What you can keep as it is

| Piece | Reuse |
| --- | --- |
| The backup server, its `rosterchief-backup` upload account, the root-only archive and the archive job (`archive-incoming.sh`, cron every 15 minutes) | **Unchanged.** It simply receives files from one more host. |
| The age key pair | **The same one.** Put the same public key (`age1…`) on the new host. The private key you already hold then opens old and new backups alike. |
| The rclone remote called `offsite` and its settings (`shell_type none`, `set_modtime false`) | The same definition, created again on the new host. |
| The restricted SSH key line in `authorized_keys` | The same line, with a **new key per production host**, so each can be revoked on its own. Old and new hosts' keys sit side by side. |

### What changes

The old `backup.sh` runs `docker compose exec db pg_dump` and `docker compose exec web tar`. The new hosts have no Docker, so those lines become native commands. Start from a copy of the old `deploy/backup.sh` and change these parts:

| In the old script | In the new one |
| --- | --- |
| `COMPOSE=…`, and reading `POSTGRES_USER`, `POSTGRES_DB`, `AWS_STORAGE_BUCKET_NAME` from `./.env` | Remove. Read `BACKUP_AGE_RECIPIENT` from `/etc/rosterchief/backup.env` (`from_env_file BACKUP_AGE_RECIPIENT /etc/rosterchief/backup.env`) and the database name from `/etc/rosterchief/site.env` (`ROSTERCHIEF_DATABASE_NAME`, default `rosterchief`). `BACKUP_REMOTE` stays on the cron line or in `backup.env`. |
| `$COMPOSE exec -T db pg_dump --clean --if-exists … \| gzip > "$DB_TMP"` | `runuser -u rosterchief -- pg_dump --format=custom --no-password --dbname "${ROSTERCHIEF_DATABASE_NAME:-rosterchief}" > "$DB_TMP"`. This is the same dump the deploy takes before migrating, and it covers every club's schema. Name it `db-$STAMP.dump` (the custom format is already compressed). Replace `gzip -t` with `pg_restore --list "$DB_TMP" > /dev/null`, and the retention pattern `db-*.sql.gz` with `db-*.dump`. |
| `$COMPOSE exec -T web tar -cz -C /app media \| cat > …` (and `private_media`) | `tar -cz -C /var/lib/rosterchief media > "$MEDIA_OUT.part"`, and the same for `private_media`. Drop the `AWS_STORAGE_BUCKET_NAME` condition: uploads are on local disk here. |

Everything else (writing to `.part` and renaming, `seal()` with age, refusing to upload without an age key, the `.offsite-sent` list that makes a failed night catch up the next one, the rclone flags) is independent of Docker and stays. Run it as **root** on the `backend` or `full` host only; frontend hosts hold no data. Optionally add a distinct filename prefix, such as `v2-`, so old and new files are easy to tell apart in the shared archive. The archive job accepts any `*.age` file.

The adapted script is committed as `deploy/host/backup.sh`. It has not been run on a new host yet, so test it with step 4 below before you trust it. A systemd timer is not included; use the cron line in step 5.

### Setting it up on the new host

Do these as root on the new backend/full host, in order.

1. **Install the tools.** Amazon Linux 2023 packages neither `age` nor `rclone`, and ships without cron:
   ```bash
   ARCH=amd64          # arm64 on Graviton (check: uname -m)
   AGE_VERSION=v1.3.2  # latest from https://github.com/FiloSottile/age/releases
   curl -fsSL -o /tmp/age.tar.gz "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${ARCH}.tar.gz"
   tar -xzf /tmp/age.tar.gz -C /tmp && install -m 0755 /tmp/age/age /tmp/age/age-keygen /usr/local/bin/ && rm -rf /tmp/age /tmp/age.tar.gz
   dnf install -y "https://downloads.rclone.org/rclone-current-linux-${ARCH}.rpm" cronie
   systemctl enable --now crond
   age --version && rclone version | head -1
   ```
2. **Configuration.** The public key is the one printed when the key pair was made (`age-keygen -o …`). Do not create a new pair unless you want one.
   ```bash
   install -d -m 700 /var/backups/rosterchief
   install -m 600 /dev/null /etc/rosterchief/backup.env
   printf 'BACKUP_AGE_RECIPIENT=age1…your public key…\nBACKUP_REMOTE=offsite:rosterchief/incoming\n' > /etc/rosterchief/backup.env
   ```
3. **An upload key for this host**, allowed on the backup server for uploading only:
   ```bash
   ssh-keygen -t ed25519 -N "" -C "rosterchief backups $(hostname)" -f /root/.ssh/rosterchief_backup
   cat /root/.ssh/rosterchief_backup.pub
   ```
   On the backup server, append this line to `~rosterchief-backup/.ssh/authorized_keys` (it stays root-owned, mode 644, as the old guide sets up). Use `/usr/libexec/openssh/sftp-server` on Amazon Linux or `/usr/lib/openssh/sftp-server` on Debian/Ubuntu, whichever `ls` shows there:
   ```
   restrict,command="/usr/libexec/openssh/sftp-server -P remove,rmdir,rename,posix-rename,setstat,fsetstat,lsetstat,symlink,hardlink,copy-data" ssh-ed25519 AAAA…the new key… rosterchief backups <hostname>
   ```
   Then, as root on the new host, check it and accept the server's host key after comparing the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the backup server:
   ```bash
   sftp -i /root/.ssh/rosterchief_backup rosterchief-backup@<backup-host>
   #   ls rosterchief        -> shows "incoming"
   #   rmdir rosterchief/incoming   -> must say "Permission denied"
   rclone config create offsite sftp host <backup-host> user rosterchief-backup \
     key_file /root/.ssh/rosterchief_backup known_hosts_file /root/.ssh/known_hosts shell_type none set_modtime false
   rclone lsd offsite:rosterchief      # lists "incoming"
   ```
4. **Install the script** from the repository (copy it over with `scp`, do not paste it into a terminal, long pastes get corrupted) as `/usr/local/sbin/rosterchief-backup` (mode 700) and run it once by hand:
   ```bash
   scp deploy/host/backup.sh <host>:/tmp/rosterchief-backup
   ssh <host> 'sudo install -m 700 -o root -g root /tmp/rosterchief-backup /usr/local/sbin/rosterchief-backup && rm /tmp/rosterchief-backup'
   BACKUP_REMOTE=offsite:rosterchief/incoming /usr/local/sbin/rosterchief-backup /var/backups/rosterchief
   ls -l /var/backups/rosterchief           # db-….dump.age, media-….tar.gz.age, private-media-….tar.gz.age
   rclone ls offsite:rosterchief/incoming   # the same files, on the backup server
   rclone deletefile offsite:rosterchief/incoming/<one of them>   # must fail with "permission denied"
   ```
   On the backup server, `sudo MIN_AGE_MINUTES=0 /usr/local/sbin/rosterchief-archive-incoming` archives them immediately; they appear in `/var/lib/rosterchief-backups/archive`, owned by root.
5. **Schedule it.** root's crontab on the new host (keep the `PATH` line, which lets cron find age in `/usr/local/bin`):
   ```cron
   MAILTO=you@example.com
   PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   30 2 * * *  /usr/local/sbin/rosterchief-backup /var/backups/rosterchief >> /var/log/rosterchief-backup.log 2>&1
   ```
   Check `/var/backups/rosterchief` for fresh files after the first night. A backup that fails silently is worse than none, so make sure mail works, or look at the log.

### Prove a backup restores (monthly, on your own computer)

Fetch last night's file from the **archive** on the backup server with your own admin login. The upload key cannot see the archive. Then decrypt it with the private key and restore it into a scratch database (PostgreSQL 16 locally):

```bash
ssh <you>@<backup-host> 'sudo ls -lt /var/lib/rosterchief-backups/archive | head'
ssh <you>@<backup-host> 'sudo cat /var/lib/rosterchief-backups/archive/db-2026-11-17-0230.dump.age' > db.dump.age
age -d -i rosterchief-backup-key.txt db.dump.age > db.dump
createdb restore_check && pg_restore --no-owner --dbname restore_check db.dump
psql restore_check -c 'select count(*) from club_club' -c 'select count(*) from club_user'   # not zero
dropdb restore_check && rm db.dump
```

Restoring onto a server is described under "Rollback and recovery" in `deploy/README.md` (restore into a **new** database, then point `app.env` and `site.env` at it).

### Things to remember

- **Pruning.** The archive job deletes files after 60 days (`ARCHIVE_KEEP_DAYS`). Before you switch the old production off, copy its last backups out of the archive, or set `ARCHIVE_KEEP_DAYS=0` on the backup server's cron line until you have.
- **What is not in the backups:** `/etc/rosterchief/app.env` (the Django secret key, VAPID private key and mail keys), `caddy.env` and `deploy.env`. Keep a copy of `app.env` in your password manager, next to the age private key. Losing the private key means the backups can never be opened.
- **The deploy's own checkpoint** (`/var/lib/rosterchief/checkpoints`, a plain `pg_dump` taken before each migration) stays on the host unencrypted and is not copied offsite. It is for rollback, not for backup. The nightly job covers offsite.
- **Frontend hosts** need no backup.

## Secret scanning (gitleaks)

The repository is public, and a secret that reaches it must be treated as leaked for good: deleting the file later does not remove it from the history, which anyone can read. [gitleaks](https://github.com/gitleaks/gitleaks) looks for credentials in every commit. It is built into three places.

**1. On your machine.** Install it once (`brew install gitleaks`) and scan before you publish:

```bash
gitleaks git --no-banner --redact -v .                          # the whole history
gitleaks git --no-banner --redact -v --log-opts development .   # only what is on a branch
gitleaks dir --no-banner --redact -v .                          # the files in the working tree, committed or not
```

Optionally stop a secret before it is ever committed, with a pre-commit hook (`.git/hooks/pre-commit`, executable): `gitleaks git --pre-commit --staged --no-banner --redact -v`.

**2. In the repository cutover** (`docs/repo-cutover.md`). `deploy/cutover/cutover.sh prepare` runs gitleaks on the history it is about to publish, plus a built-in check, and records the result. `push` **refuses** unless that scan was clean. If gitleaks is not installed the result is "unscanned" and `push` refuses too. `--allow-unscanned` overrides it, knowingly. `cutover.sh scan --new . --new-branch development` runs the same scan on its own.

**3. In CI.** The `secrets` job in `.github/workflows/ci.yml` runs gitleaks on every push to `main` over the full history. It uses `gitleaks/gitleaks-action`, which is free for repositories owned by a personal account; for an organisation-owned repository it needs a `GITLEAKS_LICENSE` secret (check the current terms before relying on it).

**Configuration.** `.gitleaks.toml` starts from gitleaks' default rules and allowlists only the example environment files (`deploy/environments/*.example`, `deploy/host/app.env.example`), whose values are placeholders. Add other false positives there as narrowly as you can (a path, or a regex for one value), or list a single finding's fingerprint in `.gitleaksignore`. Never allowlist a real secret.

**If it finds something real:**
1. **Revoke and rotate it first**, whatever it is: a GitHub token, the Cloudflare API token, a Resend key, `DJANGO_SECRET_KEY` (this logs everyone out), the VAPID private key (existing push subscriptions stop working, so only rotate it if it really leaked).
2. If nothing has been published yet, remove it from the history (for example with `git filter-repo`) and run `prepare` again. If it was already published, rotating is the fix; rewriting a public history does not undo the exposure.
3. Put it where secrets belong: `deploy/environments/*.secrets` (git-ignored), `/etc/rosterchief/*.env` on the servers, or GitHub Actions secrets. Never in a file that is committed.

**Without installing it**, Docker works too: `docker run --rm -v "$PWD:/repo:ro" ghcr.io/gitleaks/gitleaks:latest git --no-banner --redact -v --config /repo/.gitleaks.toml /repo`. (`cutover.sh` itself looks for a `gitleaks` binary, or `GITLEAKS_BIN`.)

**Status.** The scanner was run for real (the official image) over this repository's history: 246 commits, one finding, a made-up key in a test fixture (`events/tests_imports.py`), which is now allowlisted by path and value; after that the scan is clean. A secret planted in a throwaway repository was caught with the same config. The cutover gate is tested with a stand-in scanner, and the CI job has not yet run on GitHub.

## When something is wrong

| Symptom | First look |
| --- | --- |
| A deploy fails | The output of `rc deploy`; on the host `journalctl -u rosterchief-web -u rosterchief-management -u rosterchief-controlpanel --since today` |
| `rc status` says health is failing | `sudo rosterchief-health --tls` on the host shows which unit or probe fails |
| No certificate | `journalctl -u rosterchief-caddy`; usually the Cloudflare token's permissions or the zone |
| Wrong client IP in logs | `CLOUDFLARE_PROXIED` does not match the real DNS setting. Fix with `rc proxy ENV on` or `off` |
| Email not arriving | `sendtestemail` above; check the domain is verified, the from address is on it, and the provider's dashboard |
