from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("registration", "0006_registration_installments")]

    operations = [
        migrations.AddField(
            model_name="registrationbatch",
            name="legacy_invoice_history",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
