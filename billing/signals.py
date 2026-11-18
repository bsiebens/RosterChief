from django.db import connection
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django_tenants.utils import get_public_schema_name


@receiver(post_save, sender="features.Plan")
def ensure_cycle(sender, instance, created, **kwargs):
    """Every plan has at least one billing cycle (a yearly one with default terms until the platform edits it)."""
    from .models import ensure_default_cycle

    ensure_default_cycle(instance)


@receiver(post_migrate)
def seed_billing(sender, **kwargs):
    """Give existing plans a billing cycle, and let the default plan be billed (at no charge) until a price is set."""
    if sender.name != "billing" or connection.schema_name != get_public_schema_name():
        return
    import datetime

    from features.catalog import DEFAULT_PLAN
    from features.models import Plan

    from .models import PlanPrice, ensure_default_cycle

    for plan in Plan.objects.all():
        ensure_default_cycle(plan)
    free = Plan.objects.filter(code=DEFAULT_PLAN).first()
    if free is not None and not PlanPrice.objects.filter(plan=free).exists():
        PlanPrice.objects.create(plan=free, active_from=datetime.date(2000, 1, 1), amount=0)
