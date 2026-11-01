"""All writes to plans, subscriptions and overrides. Each change is recorded in ``EntitlementLog``."""

from django.db import transaction
from django.utils import timezone

from .catalog import CATALOG, DEFAULT_PLAN, DEFAULT_PLANS
from .models import EntitlementLog, Feature, FeatureOverride, Plan, PlanFeature, Subscription


def log(action, *, club=None, plan=None, feature=None, old=None, new=None, reason="", actor=None) -> EntitlementLog:
    return EntitlementLog.objects.create(club=club, plan=plan, feature=feature, action=action, old=old, new=new, reason=reason, actor=actor)


def _feature(code: str) -> Feature:
    if code not in CATALOG:
        raise ValueError(f"Unknown feature '{code}'.")
    return Feature.objects.get(code=code)


def _snapshot(sub: Subscription | None) -> dict | None:
    if sub is None:
        return None
    return {"plan": sub.plan.code, "status": sub.status, "starts_at": sub.starts_at.isoformat(), "ends_at": sub.ends_at.isoformat() if sub.ends_at else None}


@transaction.atomic
def sync_catalog() -> None:
    """Mirror the code catalog into ``Feature`` and seed the default plans if they do not exist yet."""
    for f in CATALOG.values():
        Feature.objects.update_or_create(code=f.code, defaults={"name": f.name, "description": f.description, "kind": f.kind, "is_active": True})
    Feature.objects.exclude(code__in=CATALOG).update(is_active=False)

    for code, (name, features) in DEFAULT_PLANS.items():
        plan, created = Plan.objects.get_or_create(code=code, defaults={"name": name})
        if created:
            for fcode, limit in features.items():
                PlanFeature.objects.create(plan=plan, feature=Feature.objects.get(code=fcode), limit=limit)
            log("plan_seeded", plan=plan, new={"features": features})


@transaction.atomic
def subscribe(club, plan: Plan, *, status=Subscription.ACTIVE, trial_ends_at=None, ends_at=None, actor=None, reason="") -> Subscription:
    """Move ``club`` to ``plan``: the open subscription (if any) ends now and a new one starts."""
    now = timezone.now()
    current = Subscription.objects.select_related("plan").filter(club=club, ends_at__isnull=True).first()
    old = _snapshot(current)
    if current is not None:
        current.ends_at = now
        current.save(update_fields=["ends_at", "modified"])
    sub = Subscription.objects.create(club=club, plan=plan, status=status, starts_at=now, ends_at=ends_at, trial_ends_at=trial_ends_at)
    log("subscribed", club=club, plan=plan, old=old, new=_snapshot(sub), reason=reason, actor=actor)
    return sub


@transaction.atomic
def end_subscription(club, *, actor=None, reason="") -> None:
    current = Subscription.objects.select_related("plan").filter(club=club, ends_at__isnull=True).first()
    if current is None:
        return
    old = _snapshot(current)
    current.ends_at = timezone.now()
    current.status = Subscription.CANCELED
    current.save(update_fields=["ends_at", "status", "modified"])
    log("subscription_ended", club=club, plan=current.plan, old=old, new=_snapshot(current), reason=reason, actor=actor)


def _override_snapshot(ov: FeatureOverride | None) -> dict | None:
    if ov is None:
        return None
    return {"enabled": ov.enabled, "limit": ov.limit, "expires_at": ov.expires_at.isoformat() if ov.expires_at else None}


@transaction.atomic
def set_override(club, code: str, *, enabled=True, limit=None, expires_at=None, actor=None, reason="") -> FeatureOverride:
    """Grant (``enabled=True``) or revoke (``enabled=False``) a feature for one club, regardless of its plan."""
    feature = _feature(code)
    existing = FeatureOverride.objects.filter(club=club, feature=feature).first()
    old = _override_snapshot(existing)
    ov, _ = FeatureOverride.objects.update_or_create(
        club=club, feature=feature, defaults={"enabled": enabled, "limit": limit, "expires_at": expires_at, "reason": reason}
    )
    log("override_set", club=club, feature=feature, old=old, new=_override_snapshot(ov), reason=reason, actor=actor)
    return ov


@transaction.atomic
def clear_override(club, code: str, *, actor=None, reason="") -> None:
    feature = _feature(code)
    existing = FeatureOverride.objects.filter(club=club, feature=feature).first()
    if existing is None:
        return
    old = _override_snapshot(existing)
    existing.delete()
    log("override_cleared", club=club, feature=feature, old=old, reason=reason, actor=actor)


def ensure_default_subscriptions() -> int:
    """Subscribe every club that has no subscription at all to the default plan. Returns how many were added."""
    from django_tenants.utils import get_public_schema_name, get_tenant_model

    plan = Plan.objects.filter(code=DEFAULT_PLAN).first()
    if plan is None:
        return 0
    clubs = get_tenant_model().objects.exclude(schema_name=get_public_schema_name()).filter(subscriptions__isnull=True)
    n = 0
    for club in clubs:
        subscribe(club, plan, reason="Default plan")
        n += 1
    return n
