import secrets

import formbuilder.models
from django.db import migrations, models


def backfill_unique_public_tokens(apps, schema_editor):
    # Step 2 of Django's own documented recipe for adding a unique field with
    # a callable default (docs: "migrations that add unique fields") -- the
    # AddField step's own callable default isn't guaranteed to run per-row
    # (SQLite's table-rebuild path evaluates it once for the whole copy,
    # which is exactly what produced a real UNIQUE-constraint collision here
    # with the two FormSend rows already in the dev database). Reassigning a
    # fresh token per row in plain Python, before the unique constraint is
    # added in the next step, guarantees each one is genuinely distinct.
    FormSend = apps.get_model("formbuilder", "FormSend")
    for send in FormSend.objects.all():
        send.public_token = secrets.token_urlsafe(32)
        send.save(update_fields=["public_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("formbuilder", "0010_field_section"),
    ]

    operations = [
        migrations.AddField(
            model_name="formsend",
            name="is_public",
            field=models.BooleanField(default=False, help_text="Anyone with the link can submit, no club login needed -- only takes effect if the form itself doesn't require sign-in.", verbose_name="public"),
        ),
        migrations.AddField(
            model_name="formsend",
            name="public_token",
            field=models.CharField(default=formbuilder.models._generate_public_token, editable=False, max_length=64, verbose_name="public token"),
        ),
        migrations.RunPython(backfill_unique_public_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="formsend",
            name="public_token",
            field=models.CharField(default=formbuilder.models._generate_public_token, editable=False, max_length=64, unique=True, verbose_name="public token"),
        ),
    ]
