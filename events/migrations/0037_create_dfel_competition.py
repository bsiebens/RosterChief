# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_dfel_competition(apps, schema_editor):
    """Seeds the DFEL competition (German women's ice hockey league, EHV-NRW,
    see events.competition.hockey.DFEL) with its own waffle flag, the same
    way 0017/0018/0019 seeded RBIHF and CEHL. The flag is created but not
    enabled for anyone -- a club gets it from the control panel's Features
    page. get_or_create keeps this idempotent."""
    Competition = apps.get_model("events", "Competition")
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    flag, _created = Flag.objects.get_or_create(name="DFEL")
    competition, _created = Competition.objects.get_or_create(name="DFEL", defaults={"module": "events.competition.hockey", "sport_type": "ice_hockey", "flag": flag})
    if competition.flag_id is None:
        competition.flag = flag
        competition.save(update_fields=["flag"])


def remove_dfel_competition(apps, schema_editor):
    Competition = apps.get_model("events", "Competition")
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))

    Competition.objects.filter(name="DFEL", module="events.competition.hockey").delete()
    Flag.objects.filter(name="DFEL", competitions__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0036_alter_event_external_source_id"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_dfel_competition, remove_dfel_competition),
    ]
