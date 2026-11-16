from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("registration", "0005_settle_free_registrations")]

    operations = [
        migrations.AddField(
            model_name="registrationsettings",
            name="installment_offsets",
            field=models.JSONField(blank=True, default=list, verbose_name="days between the first due date and later instalments"),
        ),
        migrations.AddField(
            model_name="registrationsettings",
            name="installment_grace_days",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="instalment signup grace period in days"),
        ),
        migrations.AddField(
            model_name="registrationsettings",
            name="installment_block_kinds",
            field=models.JSONField(blank=True, default=list, verbose_name="event types blocked by late instalments"),
        ),
        migrations.AddField(
            model_name="registrationbatch",
            name="installment_offsets",
            field=models.JSONField(blank=True, default=list, verbose_name="days between the first due date and later instalments"),
        ),
    ]
