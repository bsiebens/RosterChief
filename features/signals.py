from django.db import connection
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django_tenants.utils import get_public_schema_name


@receiver(post_migrate)
def sync_features(sender, **kwargs):
    """Keep Feature rows and default plans in step with the code catalog, and subscribe clubs without a plan."""
    if sender.name != "features" or connection.schema_name != get_public_schema_name():
        return
    from .services import ensure_default_subscriptions, sync_catalog

    sync_catalog()
    ensure_default_subscriptions()
