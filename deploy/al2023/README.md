# Amazon Linux 2023 deployment

This layout uses a dedicated Amazon Linux 2023 EC2 host, PostgreSQL 16 over a local Unix socket, Valkey on loopback, and Caddy as the public TLS endpoint. Caddy sends the apex and club domains to Management and sends `control.<domain>` to the Control Panel. Reserve the `control` subdomain for this service.

## Prepare AWS

- Use a current AL2023 package snapshot that offers `nodejs24`, `postgresql16-server`, and `valkey`. Attach a persistent EBS volume or retain the instance's EBS volume when replacing the host. Back up PostgreSQL and both media directories to separate storage.
- Point the domain and its wildcard DNS record at the host's stable public address. Open ports 80 and 443 in the security group. Keep database, Valkey, Gunicorn, and SvelteKit ports private. Use AWS Systems Manager or restrict SSH to the operator's network.
- Keep the public DNS zone in Route 53. Give the instance profile permission to find the zone and manage its ACME challenge TXT records. Follow the module's example for `ListHostedZonesByName`, `ListResourceRecordSets`, `ChangeResourceRecordSets`, and `GetChange`, restricted to the intended hosted zone and challenge names. Caddy uses the instance profile to renew the certificates. [Route 53 module and IAM policy](https://github.com/caddy-dns/route53)
- Install the operating system and the package updates for the chosen release before bootstrapping. [Amazon Linux package snapshots](https://docs.aws.amazon.com/linux/al2023/ug/deterministic-upgrades.html) · [Amazon Linux Node.js packages](https://docs.aws.amazon.com/linux/al2023/ug/nodejs.html)

## First release

Check out the reviewed commit on the host as the operator. The release command reads the committed source from that checkout.

```bash
sudo deploy/al2023/bootstrap.sh example.com admin@example.com
sudoedit /etc/rosterchief/app.env
sudo deploy/al2023/deploy.sh release "$PWD" v2.0.0
```

Bootstrap installs packages and service files, creates the local database role and database, and generates a secret key in the root-only environment file. It preserves existing app settings on re-runs. It uses the Caddy download service to obtain a build with the Route 53 module, or accepts an operator-supplied build as the third argument. [Caddy systemd guidance](https://caddyserver.com/docs/running)

Configure SMTP in `/etc/rosterchief/app.env` before inviting users or sending password resets. For Amazon SES, create SMTP credentials and verify the sender in the chosen region. The app uses the console mail backend until SMTP is configured. [Amazon SES SMTP credentials](https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html)

Set the platform owner's first password interactively. Use the same owner email as in bootstrap.

```bash
sudo systemd-run --wait --pty --collect \
  -p User=rosterchief -p WorkingDirectory=/opt/rosterchief/current \
  -p EnvironmentFile=/etc/rosterchief/app.env \
  /opt/rosterchief/current/.venv/bin/python manage.py changepassword admin@example.com
sudo /usr/local/sbin/rosterchief-health --tls
```

Then sign in at `https://control.example.com`, enroll MFA, and add or restore a club. Check that the club host and a legacy public API URL work through Caddy.

## Release and monitoring

```bash
git fetch --tags origin
sudo deploy/al2023/deploy.sh release "$PWD" v2.0.1
sudo /usr/local/sbin/rosterchief-health --tls
```

The script builds both frontends and installs the locked Python dependencies before stopping app services. It records a PostgreSQL checkpoint under `/var/lib/rosterchief/checkpoints`, migrates all schemas, switches the release symlink, and waits for the app processes and local HTTP probes. Schedule a maintenance window for the database and service restart. Caddy and its certificates persist across releases. Public and private uploads stay under `/var/lib/rosterchief`.

The systemd timer checks the app and infrastructure unit states, the PostgreSQL and Valkey health endpoint, and both frontend login routes over local TLS. Feed timer failure and service restarts into your host monitoring. Use an external HTTPS probe on `/healthz` for public DNS, routing, and certificate availability. Review Celery run history in the Control Panel for task failures.

```bash
systemctl list-timers rosterchief-health.timer
journalctl -u rosterchief-health -u rosterchief-caddy -u rosterchief-web -u rosterchief-worker -u rosterchief-beat --since today
journalctl -u rosterchief-management -u rosterchief-controlpanel --since today
```

## Rollback and recovery

For a release whose migrations remain compatible with the previous code, switch to a completed release directory.

```bash
ls -l /opt/rosterchief/releases
sudo deploy/al2023/deploy.sh rollback "/opt/rosterchief/releases/PREVIOUS_RELEASE_DIRECTORY"
sudo /usr/local/sbin/rosterchief-health --tls
```

If a migration failed or the previous code needs the prior schema, keep the app services stopped. Restore the printed pre-migration PostgreSQL checkpoint into a **new** database so the failed database remains available for inspection. Set `DJANGO_DATABASE_URL=postgresql:///rosterchief_recovery` in `app.env` and `ROSTERCHIEF_DATABASE_NAME=rosterchief_recovery` in `site.env` before activating the previous release. Recover the matching EBS or media backup as needed. A database restore returns data to the checkpoint time.

```bash
sudo systemctl stop rosterchief-web rosterchief-management rosterchief-controlpanel rosterchief-beat rosterchief-worker
sudo -u postgres createdb -O rosterchief rosterchief_recovery
sudo sh -c 'runuser -u postgres -- pg_restore --exit-on-error --no-owner --role=rosterchief --dbname=rosterchief_recovery < "$1"' \
  sh "/var/lib/rosterchief/checkpoints/CHECKPOINT_FILENAME"
```

Verify application behavior after rollback and archive the deployment checkpoint and persistent media off the host. Retain the active and last known good release directories.

## Installed club app and browser push

Open the club URL over HTTPS. Members can install it from **Me → App & notifications**. On iPhone and iPad, use Safari’s **Share → Add to Home Screen**, then open the installed app before enabling push.

Generate a key pair with `uv run python manage.py generate_vapid_keys --email you@example.com` (it prints the three lines and writes nothing). Set `DJANGO_VAPID_PUBLIC_KEY`, `DJANGO_VAPID_PRIVATE_KEY` and `DJANGO_VAPID_ADMIN_EMAIL` in `/etc/rosterchief/app.env` and restart the Django web and worker services. Use a stable VAPID key pair because existing browser subscriptions are tied to the public key. Enable Web push in **Control panel → Push**, then subscribe from the installed app and send a test notification to that user.

Use the member's notification bell to distinguish a missing invitation from a push delivery problem. A future event created with **Tell the invited people** sends an in-app message to the invited members with an account and to the parents or guardians of invited children. The account creating the event is skipped, including its copies for children. Other linked guardians still receive their own copies.

Officiating invitations use a separate job. After applying tenant migrations, check that `rosterchief-worker` and `rosterchief-beat` are running. **Control panel → Jobs** should show `events-notify-officiating-opportunities` running every ten minutes and `notifications-process-outbox` running every minute. The officiating job covers eligible games starting within 30 days and can notify the creator if they are eligible themselves or are a guardian of an eligible child. The bell does not require a browser subscription; device delivery also follows the recipient's push preferences and quiet hours.
