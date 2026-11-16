from django.core.management.base import BaseCommand

from features.services import ensure_default_subscriptions, sync_catalog


class Command(BaseCommand):
    help = "Sync the feature catalog into the database, seed missing default plans and subscribe clubs that have no plan."

    def handle(self, *args, **options):
        sync_catalog()
        added = ensure_default_subscriptions()
        self.stdout.write(f"Feature catalog synced; {added} club(s) subscribed to the default plan.")
