from django.conf import settings
from django.db import migrations


def create_public_tenant(apps, schema_editor):
    """Ensure the public schema has a tenant and a domain for the base domain."""
    Club = apps.get_model("club", "Club")
    Domain = apps.get_model("club", "Domain")

    public, _ = Club.objects.get_or_create(schema_name="public", defaults={"name": "Public"})
    Domain.objects.get_or_create(
        domain=settings.ROSTERCHIEF_BASE_DOMAIN,
        defaults={"tenant": public, "is_primary": True},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_public_tenant, migrations.RunPython.noop),
    ]
