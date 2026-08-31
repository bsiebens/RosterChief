# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_officials_flag(apps, schema_editor):
    """Ensures the "officials" waffle flag (events.services.officials.
    OFFICIALS_FLAG) exists in production, the same way migration 0018's
    create_flags_for_seeded_competitions seeded a flag per competition.

    Match officials (teams.models.OfficialLevel/OfficialProfile, events.models.
    EventOfficial/OfficialSignup, the "Officials" panel on the event detail
    page and the referee-management dashboard) is feature-flagged, opt-in per
    club -- not every sport needs a second kind of match official the way it
    needs referees. Without this migration, the flag simply doesn't exist
    until the first club admin visits the control panel's Features page and
    creates it by hand, which would otherwise be the only way to ever turn
    officials on for anyone. This just makes sure the row is there to turn
    on per club from that same page; it deliberately does NOT set
    ``everyone`` (stays at its default, unset value), so the feature starts
    off disabled everywhere, exactly as if a club admin had created the flag
    themselves and not enabled it for anyone yet.

    get_or_create makes this safe to run whether or not the flag already
    exists (e.g. a club admin already created it by hand before this
    migration ever ran) -- it's a no-op either way, and running this
    migration twice (a squash, a replayed fixture load, ...) never errors.
    """
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    Flag.objects.get_or_create(name="officials")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0030_event_live_score_polling_done_at"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_officials_flag, noop),
    ]
