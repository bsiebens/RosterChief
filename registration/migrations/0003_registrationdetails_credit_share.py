from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0002_registrationdetails_product"),
    ]

    operations = [
        migrations.AddField(
            model_name="registrationdetails",
            name="credit_share",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="credit share"),
        ),
    ]
