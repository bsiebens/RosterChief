"""Price the registrations that are still waiting for review again with the current pricing rules.

Registrations submitted under older rules can carry discounts the rules no longer give (for example a registrant tier that counted every person
instead of the people on the same product). Confirmed and cancelled registrations are never touched. Run for every club, or one with --schema:

    manage.py reprice_registrations [--schema club_schema] [--dry-run]
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

from registration.models import RegistrationBatch
from registration.services import invoicing


class Command(BaseCommand):
    help = "Reprice the registrations waiting for review with the current pricing rules (confirmed and cancelled ones are never touched)."

    def add_arguments(self, parser):
        parser.add_argument("--schema", help="Only this club's schema.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, write nothing.")

    def handle(self, *args, schema=None, dry_run=False, **options):
        clubs = get_tenant_model().objects.exclude(schema_name=get_public_schema_name())
        if schema:
            clubs = clubs.filter(schema_name=schema)
        total = 0
        for club in clubs:
            with schema_context(club.schema_name):
                changed = 0
                try:
                    with transaction.atomic():
                        for batch in RegistrationBatch.objects.filter(confirmed_at__isnull=True, cancelled_at__isnull=True):
                            if invoicing.reprice(None, batch):
                                changed += 1
                                self.stdout.write(f"{club.schema_name}: repriced registration {batch.pk}")
                        if dry_run:
                            transaction.set_rollback(True)
                except Exception as error:  # one club failing must not stop the others
                    self.stderr.write(f"{club.schema_name}: {error}")
                total += changed
        self.stdout.write(f"{'Would reprice' if dry_run else 'Repriced'} {total} registration(s).")
