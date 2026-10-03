# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_ijnl_competition(apps, schema_editor):
    """Seeds the IJNL competition (IJshockey Nederland, ijshockey.nl, see
    events.competition.hockey.IJNL) with its own waffle flag, the same way
    0037 seeded DFEL. The flag is created but not
    enabled for anyone -- a club gets it from the control panel's Features
    page. get_or_create keeps this idempotent."""
    Competition = apps.get_model("events", "Competition")
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    flag, _created = Flag.objects.get_or_create(name="IJNL")
    competition, _created = Competition.objects.get_or_create(name="IJNL", defaults={"module": "events.competition.hockey", "sport_type": "ice_hockey", "flag": flag})
    if competition.flag_id is None:
        competition.flag = flag
        competition.save(update_fields=["flag"])


def remove_ijnl_competition(apps, schema_editor):
    Competition = apps.get_model("events", "Competition")
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    Competition.objects.filter(name="IJNL", module="events.competition.hockey").delete()
    Flag.objects.filter(name="IJNL", competitions__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0038_settle_signups_for_assigned_members"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_ijnl_competition, remove_ijnl_competition),
    ]
