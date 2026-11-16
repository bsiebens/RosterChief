"""Fill someone's in-app inbox with sample notifications, to try the bell: ``manage.py inbox_demo <club-slug> <email>``."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from club.models import Club
from notifications import inbox

SAMPLES = [
    ("warning", "Membership awaiting approval", "A new sign-up is waiting to be activated.", "/members?kind=member&status=pending"),
    ("success", "Import finished", "24 members were imported, 2 skipped as duplicates.", "/members"),
    ("warning", "Team without coach", "A team has nobody with a management position this season.", "/teams?coach=none"),
    ("info", "Referee level expires soon", "A referee level is valid until the end of this month.", "/teams/officials"),
]


class Command(BaseCommand):
    help = "Create sample inbox notifications for a user in a club."

    def add_arguments(self, parser):
        parser.add_argument("club_slug")
        parser.add_argument("email")

    def handle(self, club_slug, email, **options):
        with schema_context(get_public_schema_name()):
            club = Club.objects.filter(slug=club_slug).first()
            user = get_user_model().objects.filter(email__iexact=email).first()
            if club is None or user is None:
                raise CommandError("Unknown club or user.")
            for kind, title, body, href in SAMPLES:
                inbox.notify(user, club, title=title, body=body, kind=kind, href=href)
        self.stdout.write(f"Created {len(SAMPLES)} notifications for {email} in {club.name}.")
