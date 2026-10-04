# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_flag(apps, schema_editor):
    """Seeds the "game_timeline" flag (events.services.game_timeline): game
    timelines are only fetched and stored for clubs it's on for, on top of the
    competition's own flag. Created but not enabled for anyone -- a club gets it
    from the control panel's Features page. get_or_create keeps this idempotent."""
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))
    Flag.objects.get_or_create(name="game_timeline")


def remove_flag(apps, schema_editor):
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))
    Flag.objects.filter(name="game_timeline").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0041_game_timeline"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_flag, remove_flag),
    ]
