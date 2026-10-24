# Generated manually -- data migration, no schema change.

from django.conf import settings
from django.db import migrations


def create_flag(apps, schema_editor):
    """Seeds the "analytics" flag (the team analytics page). Created but not enabled for
    anyone -- a club gets it from the control panel's Features page."""
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))
    Flag.objects.get_or_create(name="analytics")


def remove_flag(apps, schema_editor):
    Flag = apps.get_model(*settings.WAFFLE_FLAG_MODEL.split("."))
    Flag.objects.filter(name="analytics").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0052_game_stat"),
        migrations.swappable_dependency(settings.WAFFLE_FLAG_MODEL),
    ]

    operations = [
        migrations.RunPython(create_flag, remove_flag),
    ]
