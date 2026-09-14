# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_tournaments_flag(apps, schema_editor):
    """Ensures the "tournaments" waffle flag exists, the same way evaluations'
    migration 0002_create_evaluations_flag seeded "evaluations".

    Tournaments are opt-in per club -- not every club runs them. Without this
    migration, the flag simply doesn't exist until the first club admin
    visits the control panel's Features page and creates it by hand, which
    would otherwise be the only way to ever turn tournaments on for anyone.
    This just makes sure the row is there to turn on per club from that same
    page; it deliberately does NOT set ``everyone`` (stays at its default,
    unset value) and attaches no clubs, so the feature starts off disabled
    everywhere, exactly as if a club admin had created the flag themselves
    and not enabled it for anyone yet.

    get_or_create makes this safe to run whether or not the flag already
    exists, and safe to run twice (a squash, a replayed fixture load, ...).
    """
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    Flag.objects.get_or_create(name="tournaments")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0001_initial"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_tournaments_flag, noop),
    ]
