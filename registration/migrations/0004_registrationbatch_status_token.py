import secrets

from django.db import migrations, models

import registration.models


def backfill_status_tokens(apps, schema_editor):
    RegistrationBatch = apps.get_model("registration", "RegistrationBatch")
    for batch in RegistrationBatch.objects.filter(status_token=""):
        batch.status_token = secrets.token_urlsafe(32)
        batch.save(update_fields=["status_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0003_registration_details_membership_fk"),
    ]

    operations = [
        # Nullable/non-unique first -- a callable default can't backfill unique
        # values across existing rows at the SQL level (makemigrations itself
        # refuses this in one step). Steps 2/3 below do that in Python, then
        # lock the constraint down once every row actually has one.
        migrations.AddField(
            model_name="registrationbatch",
            name="status_token",
            field=models.CharField(blank=True, default="", editable=False, max_length=64, verbose_name="status token"),
        ),
        migrations.RunPython(backfill_status_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="registrationbatch",
            name="status_token",
            field=models.CharField(default=registration.models._generate_status_token, editable=False, max_length=64, unique=True, verbose_name="status token"),
        ),
    ]
