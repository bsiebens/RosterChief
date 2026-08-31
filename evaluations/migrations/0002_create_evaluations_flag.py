# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_evaluations_flag(apps, schema_editor):
    """Ensures the "evaluations" waffle flag (club.mixins.
    EvaluationManagerRequiredMixin.feature_flag) exists, the same way events'
    migration 0031_create_officials_flag seeded "officials".

    Player evaluations are opt-in per club -- not every club wants a formal
    rubric. Without this migration, the flag simply doesn't exist until the
    first club admin visits the control panel's Features page and creates it
    by hand, which would otherwise be the only way to ever turn evaluations
    on for anyone. This just makes sure the row is there to turn on per
    club from that same page; it deliberately does NOT set ``everyone``
    (stays at its default, unset value) and attaches no clubs, so the
    feature starts off disabled everywhere, exactly as if a club admin had
    created the flag themselves and not enabled it for anyone yet.

    get_or_create makes this safe to run whether or not the flag already
    exists, and safe to run twice (a squash, a replayed fixture load, ...).
    """
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    Flag.objects.get_or_create(name="evaluations")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0001_initial"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_evaluations_flag, noop),
    ]
