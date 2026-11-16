from django.conf import settings
from django.core.management.base import BaseCommand
from tenant_users.tenants.utils import create_public_tenant

from club.models import Club
from club.services import mark_email_verified


class Command(BaseCommand):
    help = "Create the public tenant, its base-domain Domain and the platform owner (superuser). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--owner-email", required=True)

    def handle(self, *args, **options):
        public = Club.objects.filter(schema_name="public").first()
        if public:
            mark_email_verified(public.owner)
            self.stdout.write("Public tenant already exists; ensured the owner's email is verified.")
            return
        _tenant, _domain, owner = create_public_tenant(
            domain_url=settings.ROSTERCHIEF_BASE_DOMAIN,
            owner_email=options["owner_email"],
            is_superuser=True,
            is_staff=True,
        )
        mark_email_verified(owner)
        self.stdout.write(self.style.SUCCESS(f"Public tenant created for {settings.ROSTERCHIEF_BASE_DOMAIN}. Set a password with: manage.py changepassword {options['owner_email']}"))
