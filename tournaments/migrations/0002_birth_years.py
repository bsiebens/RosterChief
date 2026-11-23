from django.db import migrations, models


def ages_to_birth_years(apps, schema_editor):
    """Carry any existing age limits over: age on the start date -> birth year (the youngest allowed is born latest)."""
    Tournament = apps.get_model("tournaments", "Tournament")
    for t in Tournament.objects.exclude(min_age__isnull=True, max_age__isnull=True):
        year = t.start_date.year
        t.earliest_birth_year = year - t.max_age if t.max_age is not None else None
        t.latest_birth_year = year - t.min_age if t.min_age is not None else None
        t.save(update_fields=["earliest_birth_year", "latest_birth_year"])


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0001_initial")]

    operations = [
        migrations.AddField(model_name="tournament", name="earliest_birth_year", field=models.PositiveSmallIntegerField(blank=True, null=True, help_text="Players born before this year are flagged for staff to check, never blocked. Leave blank for no limit.", verbose_name="earliest birth year")),
        migrations.AddField(model_name="tournament", name="latest_birth_year", field=models.PositiveSmallIntegerField(blank=True, null=True, help_text="Players born after this year are flagged for staff to check, never blocked. Leave blank for no limit.", verbose_name="latest birth year")),
        migrations.RunPython(ages_to_birth_years, migrations.RunPython.noop),
        migrations.RemoveField(model_name="tournament", name="min_age"),
        migrations.RemoveField(model_name="tournament", name="max_age"),
    ]
