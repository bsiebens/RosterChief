from django.db import migrations

from rosterchief.base import unique_slugify


def backfill_slugs(apps, schema_editor):
    """News.save() already auto-populates a blank slug (rosterchief.base.
    ClubScopedModel.save), but that only fires on save() -- rows created
    before the slug field existed, or via bulk_create, never got one."""
    News = apps.get_model("news", "News")
    for item in News.objects.filter(slug=""):
        item.slug = unique_slugify(item, item.title, scope={"club_id": item.club_id})
        item.save(update_fields=["slug"])


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_slugs, migrations.RunPython.noop),
    ]
