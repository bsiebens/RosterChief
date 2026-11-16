"""Print a fresh VAPID key pair for web push, ready to paste into the app environment (``.env`` locally, ``/etc/rosterchief/app.env`` on a
server). Nothing is written anywhere. Keep the pair: browser subscriptions are tied to the public key, so a new pair makes every existing
subscription stop working."""

from django.core.management.base import BaseCommand
from py_vapid import Vapid

from notifications.gateways import _vapid_public_key


class Command(BaseCommand):
    help = "Print a new VAPID key pair (DJANGO_VAPID_PUBLIC_KEY / DJANGO_VAPID_PRIVATE_KEY) for web push."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="admin@example.com", help="Contact address for DJANGO_VAPID_ADMIN_EMAIL (push services may use it to reach you).")

    def handle(self, *args, **options):
        vapid = Vapid()
        vapid.generate_keys()
        private = vapid.private_pem().decode().strip().replace("\n", "\\n")
        self.stdout.write(f"DJANGO_VAPID_PUBLIC_KEY={_vapid_public_key(vapid)}")
        self.stdout.write(f'DJANGO_VAPID_PRIVATE_KEY="{private}"')
        self.stdout.write(f"DJANGO_VAPID_ADMIN_EMAIL={options['email']}")
        self.stderr.write("Add these to the environment, restart the web and worker processes, then switch Web push on in Control panel → Push.")
