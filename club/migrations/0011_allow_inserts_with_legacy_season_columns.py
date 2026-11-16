"""Allow current club inserts on databases upgraded from split season dates."""

from django.db import migrations
from django_tenants.utils import get_public_schema_name


def allow_inserts(apps, schema_editor):
    connection = schema_editor.connection
    if connection.schema_name != get_public_schema_name():
        return

    table = apps.get_model("club", "Club")._meta.db_table
    with connection.cursor() as cursor:
        columns = {
            column.name: column
            for column in connection.introspection.get_table_description(cursor, table)
        }
    for name in ("season_start_day", "season_start_month"):
        if name in columns and not columns[name].null_ok:
            schema_editor.execute(
                f"ALTER TABLE {schema_editor.quote_name(table)} "
                f"ALTER COLUMN {schema_editor.quote_name(name)} DROP NOT NULL"
            )


class Migration(migrations.Migration):
    dependencies = [("club", "0010_seed_competitions")]
    operations = [migrations.RunPython(allow_inserts, migrations.RunPython.noop)]
