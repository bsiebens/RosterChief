from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

from authorization.services import seed_default_roles, sync_permissions


class Command(BaseCommand):
    help = "Sync catalog permissions and the Owner role into every club schema."

    def handle(self, *args, **options):
        for club in get_tenant_model().objects.exclude(schema_name="public"):
            with schema_context(club.schema_name):
                sync_permissions()
                seed_default_roles()
            self.stdout.write(f"synced {club.schema_name}")
