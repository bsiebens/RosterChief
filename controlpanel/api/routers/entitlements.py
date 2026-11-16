"""Plans, per-club subscriptions, feature overrides, effective entitlements and the entitlement log."""

from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.pagination import paginate

from billing.models import ClubBilling
from club.models import Club
from features import services
from features.catalog import CATALOG
from features.entitlements import resolve_entitlements
from features.models import EntitlementLog, Feature, FeatureOverride, Plan, Subscription

from ..errors import ApiError
from ..idempotency import idempotent
from ..schemas import (
    EndSubscriptionIn,
    EntitlementLogOut,
    EntitlementOut,
    FeatureOut,
    OverrideIn,
    OverrideOut,
    PlanCreateIn,
    PlanOut,
    PlanUpdateIn,
    SubscribeIn,
    SubscriptionOut,
)

router = Router(tags=["entitlements"])


def _club(club_id: UUID) -> Club:
    return get_object_or_404(Club.objects.exclude(schema_name="public"), pk=club_id)


def _plan_error(e: services.PlanError, field: str = "features") -> ApiError:
    return ApiError(422, "validation_error", str(e), {field: [str(e)]})


# --- feature catalog ----------------------------------------------------------------------------------------------


@router.get("/features", response=list[FeatureOut], summary="The feature catalog (defined in code, read-only)")
def list_features(request):
    rows = {f.code: f for f in Feature.objects.all()}
    return [FeatureOut(code=d.code, name=d.name, description=d.description, kind=d.kind, limit_scope=d.limit_scope, is_active=rows[d.code].is_active if d.code in rows else False) for d in CATALOG.values()]


# --- plans --------------------------------------------------------------------------------------------------------


@router.get("/plans", response=list[PlanOut], summary="Plans and the features they include")
@paginate
def list_plans(request, include_inactive: bool = True):
    qs = Plan.objects.order_by("name")
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return [PlanOut.of(p) for p in qs]


@router.post("/plans", response={201: PlanOut}, summary="Create a plan")
def create_plan(request, payload: PlanCreateIn):
    try:
        plan = services.create_plan(code=payload.code, name=payload.name, description=payload.description, features=[(f.code, f.limit) for f in payload.features], actor=request.user)
    except services.PlanError as e:
        raise _plan_error(e) from e
    return 201, PlanOut.of(plan)


@router.get("/plans/{plan_id}", response=PlanOut)
def get_plan(request, plan_id: UUID):
    return PlanOut.of(get_object_or_404(Plan, pk=plan_id))


@router.patch("/plans/{plan_id}", response=PlanOut, summary="Edit a plan; 'features' replaces its feature set")
def update_plan(request, plan_id: UUID, payload: PlanUpdateIn):
    plan = get_object_or_404(Plan, pk=plan_id)
    features = None if payload.features is None else [(f.code, f.limit) for f in payload.features]
    try:
        services.update_plan(plan, name=payload.name, description=payload.description, is_active=payload.is_active, features=features, actor=request.user)
    except services.PlanError as e:
        raise _plan_error(e) from e
    return PlanOut.of(plan)


@router.delete("/plans/{plan_id}", response={204: None}, summary="Delete a plan that was never used (otherwise deactivate it)")
def delete_plan(request, plan_id: UUID):
    plan = get_object_or_404(Plan, pk=plan_id)
    try:
        services.delete_plan(plan, actor=request.user)
    except services.PlanError as e:
        raise ApiError(409, "plan_in_use", str(e)) from e
    return 204, None


# --- subscriptions ------------------------------------------------------------------------------------------------


@router.get("/clubs/{club_id}/subscriptions", response=list[SubscriptionOut], summary="A club's subscription history, newest first")
@paginate
def club_subscriptions(request, club_id: UUID):
    club = _club(club_id)
    return [SubscriptionOut.of(s) for s in Subscription.objects.filter(club=club).select_related("plan").order_by("-starts_at")]


@router.post("/clubs/{club_id}/subscriptions", response={201: SubscriptionOut}, summary="Move a club to a plan (its current subscription ends now)")
@idempotent
def subscribe_club(request, club_id: UUID, payload: SubscribeIn):
    club = _club(club_id)
    plan = get_object_or_404(Plan, pk=payload.plan_id)
    if ClubBilling.objects.filter(club=club).exists():
        raise ApiError(409, "billing_error", "This club is billed: use the billing plan change so the current period is prorated and a new cycle starts.")
    if not plan.is_active:
        raise ApiError(422, "validation_error", "That plan is not active.", {"plan_id": ["That plan is not active."]})
    if payload.status not in dict(Subscription.STATUS_CHOICES):
        raise ApiError(422, "validation_error", "Unknown status.", {"status": [f"Use one of: {', '.join(dict(Subscription.STATUS_CHOICES))}."]})
    sub = services.subscribe(club, plan, status=payload.status, trial_ends_at=payload.trial_ends_at, ends_at=payload.ends_at, actor=request.user, reason=payload.reason)
    return 201, SubscriptionOut.of(sub)


@router.post("/clubs/{club_id}/subscriptions/end", response={204: None}, summary="End the current subscription now (the club falls back to the default plan)")
def end_club_subscription(request, club_id: UUID, payload: EndSubscriptionIn):
    services.end_subscription(_club(club_id), actor=request.user, reason=payload.reason)
    return 204, None


# --- overrides & effective entitlements ---------------------------------------------------------------------------


@router.get("/clubs/{club_id}/entitlements", response=list[EntitlementOut], summary="What the club can use right now, and why")
def club_entitlements(request, club_id: UUID):
    ent = resolve_entitlements(_club(club_id))
    out = []
    for d in CATALOG.values():
        e = ent.entitlement(d.code)
        out.append(EntitlementOut(code=d.code, name=d.name, kind=d.kind, enabled=e.enabled, limit=e.limit if e.enabled else None, source=e.source or "none"))
    return out


@router.get("/clubs/{club_id}/overrides", response=list[OverrideOut], summary="Per-club exceptions to the plan")
def list_overrides(request, club_id: UUID):
    return [OverrideOut.of(o) for o in FeatureOverride.objects.filter(club=_club(club_id)).select_related("feature").order_by("feature__code")]


@router.put("/clubs/{club_id}/overrides/{feature_code}", response=OverrideOut, summary="Grant or revoke a feature for one club (enabled=false revokes)")
def put_override(request, club_id: UUID, feature_code: str, payload: OverrideIn):
    club = _club(club_id)
    if feature_code not in CATALOG:
        raise ApiError(404, "not_found", f"Unknown feature '{feature_code}'.")
    try:
        ov = services.set_override(club, feature_code, enabled=payload.enabled, limit=payload.limit, expires_at=payload.expires_at, actor=request.user, reason=payload.reason)
    except services.PlanError as e:
        raise _plan_error(e, "limit") from e
    return OverrideOut.of(ov)


@router.delete("/clubs/{club_id}/overrides/{feature_code}", response={204: None}, summary="Remove an override (the plan applies again)")
def delete_override(request, club_id: UUID, feature_code: str):
    club = _club(club_id)
    if feature_code not in CATALOG:
        raise ApiError(404, "not_found", f"Unknown feature '{feature_code}'.")
    services.clear_override(club, feature_code, actor=request.user, reason="Removed via API")
    return 204, None


# --- log ----------------------------------------------------------------------------------------------------------


class LogFilters(Schema):
    club_id: UUID | None = None
    action: str | None = None


@router.get("/entitlement-log", response=list[EntitlementLogOut], summary="Entitlement changes, newest first")
@paginate
def entitlement_log(request, filters: LogFilters = Query(...)):
    qs = EntitlementLog.objects.select_related("plan", "feature", "actor")
    for field, value in filters.dict(exclude_none=True).items():
        qs = qs.filter(**{field: value})
    return qs
