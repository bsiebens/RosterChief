"""All writes to plans, subscriptions and overrides. Each change is recorded in ``EntitlementLog``."""

from django.db import models, transaction
from django.utils import timezone

from .catalog import CATALOG, DEFAULT_PLAN, DEFAULT_PLANS, LIMIT, duplicate_limit_scopes
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


def _current_subscriptions(club, now):
    """Subscriptions in force right now: started, and not ended (including ones scheduled to end later)."""
    return Subscription.objects.select_related("plan").filter(club=club, starts_at__lte=now).filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))


@transaction.atomic
def subscribe(club, plan: Plan, *, status=Subscription.ACTIVE, trial_ends_at=None, ends_at=None, actor=None, reason="") -> Subscription:
    """Move ``club`` to ``plan``: the open subscription (if any) ends now and a new one starts."""
    now = timezone.now()
    current = list(_current_subscriptions(club, now))
    old = _snapshot(current[0]) if current else None
    for c in current:
        c.ends_at = now
        c.save(update_fields=["ends_at", "modified"])
    sub = Subscription.objects.create(club=club, plan=plan, status=status, starts_at=now, ends_at=ends_at, trial_ends_at=trial_ends_at)
    log("subscribed", club=club, plan=plan, old=old, new=_snapshot(sub), reason=reason, actor=actor)
    return sub


@transaction.atomic
def convert_trial(club, *, actor=None, reason="") -> Subscription | None:
    """Turn the club's open TRIAL subscription into an ACTIVE one on the same plan (no entitlement change)."""
    sub = Subscription.objects.select_related("plan").filter(club=club, ends_at__isnull=True, status=Subscription.TRIAL).first()
    if sub is None:
        return None
    old = _snapshot(sub)
    sub.status, sub.trial_ends_at = Subscription.ACTIVE, None
    sub.save(update_fields=["status", "trial_ends_at", "modified"])
    log("trial_converted", club=club, plan=sub.plan, old=old, new=_snapshot(sub), reason=reason, actor=actor)
    return sub


@transaction.atomic
def end_subscription(club, *, actor=None, reason="") -> None:
    """End the club's current subscription now. The club then falls back to the default plan."""
    now = timezone.now()
    for current in _current_subscriptions(club, now):
        old = _snapshot(current)
        current.ends_at = now
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
    if limit is not None and CATALOG[code].kind != LIMIT:
        raise PlanError(f"Feature '{code}' is on/off and cannot have a limit.")
    existing = FeatureOverride.objects.filter(club=club, feature=feature).first()
    old = _override_snapshot(existing)
    ov, _ = FeatureOverride.objects.update_or_create(club=club, feature=feature, defaults={"enabled": enabled, "limit": limit, "expires_at": expires_at, "reason": reason})
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


class PlanError(ValueError):
    pass


def _check_plan_features(items) -> dict[str, int | None]:
    """Validate ``[(code, limit)]`` for a plan: known codes, no duplicates, limits only on limit features, one per scope."""
    out: dict[str, int | None] = {}
    for code, limit in items:
        if code not in CATALOG:
            raise PlanError(f"Unknown feature '{code}'.")
        if code in out:
            raise PlanError(f"Feature '{code}' is listed twice.")
        if limit is not None and CATALOG[code].kind != LIMIT:
            raise PlanError(f"Feature '{code}' is on/off and cannot have a limit.")
        out[code] = limit
    if duplicate_limit_scopes(out):
        raise PlanError("A plan can only have one feature limiting the same thing (e.g. the number of members).")
    return out


def _set_plan_features(plan: Plan, features: dict[str, int | None]) -> None:
    PlanFeature.objects.filter(plan=plan).exclude(feature__code__in=features).delete()
    for code, limit in features.items():
        PlanFeature.objects.update_or_create(plan=plan, feature=_feature(code), defaults={"limit": limit})


@transaction.atomic
def create_plan(*, code: str, name: str, description: str = "", features=(), actor=None) -> Plan:
    if Plan.objects.filter(code=code).exists():
        raise PlanError(f"A plan with code '{code}' already exists.")
    checked = _check_plan_features(features)
    plan = Plan.objects.create(code=code, name=name, description=description)
    _set_plan_features(plan, checked)
    log("plan_created", plan=plan, new={"name": name, "features": checked}, actor=actor)
    return plan


@transaction.atomic
def update_plan(plan: Plan, *, name=None, description=None, is_active=None, features=None, actor=None) -> Plan:
    """Change a plan. ``features`` (a list of ``(code, limit)``) replaces the plan's feature set when given."""
    old = {"name": plan.name, "is_active": plan.is_active, "features": {pf.feature.code: pf.limit for pf in plan.plan_features.select_related("feature")}}
    if is_active is False and plan.code == DEFAULT_PLAN:
        raise PlanError("The default plan cannot be deactivated: clubs without a plan fall back to it.")
    checked = _check_plan_features(features) if features is not None else None
    for field, value in (("name", name), ("description", description), ("is_active", is_active)):
        if value is not None:
            setattr(plan, field, value)
    plan.save()
    if checked is not None:
        _set_plan_features(plan, checked)
    new = {"name": plan.name, "is_active": plan.is_active, "features": {pf.feature.code: pf.limit for pf in plan.plan_features.select_related("feature")}}
    log("plan_updated", plan=plan, old=old, new=new, actor=actor)
    return plan


@transaction.atomic
def delete_plan(plan: Plan, *, actor=None) -> None:
    """Delete a plan nobody has ever used. Plans with subscriptions are deactivated instead."""
    if plan.code == DEFAULT_PLAN:
        raise PlanError("The default plan cannot be deleted.")
    if Subscription.objects.filter(plan=plan).exists():
        raise PlanError("This plan has subscriptions (current or past). Deactivate it instead.")
    log("plan_deleted", old={"code": plan.code, "name": plan.name}, actor=actor)
    plan.delete()
