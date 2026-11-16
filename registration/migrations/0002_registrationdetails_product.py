import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0001_initial"),
        ("shop", "0009_system_product_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="registrationdetails",
            name="product",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="shop.product", verbose_name="product"),
        ),
        migrations.AlterField(
            model_name="registrationdetails",
            name="product_variant",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="shop.productvariant", verbose_name="variant"),
        ),
    ]
