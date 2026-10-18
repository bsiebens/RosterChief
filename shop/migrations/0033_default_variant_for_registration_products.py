from django.db import migrations


def add_default_variants(apps, schema_editor):
    """Registration products saved without any option get the product-named one (see
    shop.services.variants), so they appear in the registration dropdown."""
    Product = apps.get_model("shop", "Product")
    ProductVariant = apps.get_model("shop", "ProductVariant")
    for product in Product.objects.filter(product_type__in=["membership", "event_fee"], variants__isnull=True):
        ProductVariant.objects.create(product=product, name=product.name)


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0032_inventory"),
    ]

    operations = [
        migrations.RunPython(add_default_variants, migrations.RunPython.noop),
    ]
