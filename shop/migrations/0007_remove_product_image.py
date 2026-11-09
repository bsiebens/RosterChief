from django.db import migrations


class Migration(migrations.Migration):
    """The single product picture lives in the gallery now (see 0006).

    The column is dropped only when it is still there: an earlier version of 0006 already removed it (without copying the pictures),
    and a database that applied that version must not fail here."""

    dependencies = [
        ("shop", "0006_product_gallery_attachments"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE shop_product DROP COLUMN IF EXISTS image",
                    reverse_sql="ALTER TABLE shop_product ADD COLUMN IF NOT EXISTS image varchar(255) NOT NULL DEFAULT ''",
                ),
            ],
            state_operations=[
                migrations.RemoveField(model_name="product", name="image"),
            ],
        ),
    ]
