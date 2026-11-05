"""Generate recurring-series occurrences up to the rolling horizon: ``manage.py extend_event_series [--club <slug>]``.

The same work as the daily ``events.extend_event_series`` task; without ``--club`` every active club is processed."""

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

from events.services.recurrence import extend_all_series, horizon


class Command(BaseCommand):
    help = "Materialise recurring event occurrences up to the rolling horizon."

    def add_arguments(self, parser):
        parser.add_argument("--club", help="Only this club (slug).")

    def handle(self, *args, club=None, **options):
        clubs = get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name())
        if club:
            clubs = clubs.filter(slug=club)
            if not clubs.exists():
                raise CommandError(f"Unknown club: {club}")
        until = horizon()
        total_series = total_created = 0
        for tenant in clubs:
            with schema_context(tenant.schema_name):
                series, created = extend_all_series(until)
            total_series += series
            total_created += created
            if created:
                self.stdout.write(f"{tenant.slug}: generated {created} occurrence(s).")
        self.stdout.write(f"Generated {total_created} occurrence(s) across {total_series} series.")
