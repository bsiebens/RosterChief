"""Push notifications for the platform: monitoring, configuration, the sandbox, the test composer and a user's devices. Superusers only."""

import json
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema

from controlpanel import audit
from controlpanel.services import push_config as cfg
from controlpanel.services import push_monitoring as mon
from notifications.models import (
    Category,
    MasterSwitch,
    NotificationDelivery,
    NotificationPreference,
    PushDevice,
    PushSettings,
    QuietHours,
    SandboxEvent,
    SandboxMessage,
    SandboxState,
    UserNotificationSettings,
    WebPushSubscription,
)

from ..auth import superuser_auth
from ..errors import ApiError
from ..push_schemas import (
    DATA_LIMIT_BYTES,
    CredentialsCheckOut,
    DeletedOut,
    MonitoringOut,
    PushConfigOut,
    PushSettingsIn,
    PushSettingsOut,
    SandboxMessagesOut,
    SandboxModeIn,
    SandboxOut,
    SandboxScenarioIn,
    SendIn,
    SendOut,
    TargetOut,
    UserPushOut,
)

router = Router(tags=["push"], auth=superuser_auth)

SCENARIO_TEXT = {
    "ok": "Every message is captured as delivered.",
    "throttle": "The provider answers 429 Too Many Requests; the delivery is retried.",
    "dead": "The provider reports the token as gone; the device is switched off. Local development only.",
    "error": "The provider refuses the message; no retry.",
    "timeout": "The provider does not answer in time; the delivery is retried.",
}
SANDBOX_MINUTES = (30, 60, 240, 1440)


def _club(obj):
    return {"id": obj.pk, "name": obj.name} if obj else None


# --- monitoring -------------------------------------------------------------------------------------------------------


class MonitoringQuery(Schema):
    window: str | None = None
    club: str | None = None


@router.get("/push/monitoring", response=MonitoringOut, summary="Delivery health, retry queue, failures and device counts")
def monitoring(request, q: MonitoringQuery = Query(...)):
    from notifications import gateways, health, sandbox

    window = mon.parse_window(q.window)
    now = timezone.now()
    since = now - mon.WINDOWS[window]
    club = mon.parse_club(q.club)
    row = PushSettings.get()

    stats = health.provider_health(since, club=club)
    providers = []
    for provider, label in cfg.PROVIDERS.items():
        s = dict(stats.get(provider) or {})
        providers.append({**_empty_health(provider, label), **s, "provider": provider, "label": label, "configured": bool(gateways.credentials_status(provider).get("configured")), "enabled": getattr(row, cfg.ENABLED_FIELD[provider])})
    if "email" in stats:  # the email card only exists once the engine reports an email channel
        providers.append({**_empty_health("email", "Email"), **stats["email"], "provider": "email", "label": "Email", "configured": None, "enabled": None})

    state = sandbox.get_state()
    return {
        "window": window,
        "since": since,
        "club_id": club.pk if club else None,
        "alerts": mon.open_alerts(),
        "providers": providers,
        "retry_queue": health.retry_queue(),
        "outbox": health.outbox_stats(),
        "series": mon.series(window, now, club),
        "top_failures": mon.top_failures(since, club),
        "recent_problems": mon.recent_problems(since, club),
        "devices": mon.device_counts(row, now, club),
        "sandbox": {"enabled": bool(state.get("effective")), "expires_at": state.get("expires_at")},
    }


def _empty_health(provider, label):
    return {"provider": provider, "label": label, "requests": 0, "sent": 0, "failed": 0, "dead": 0, "retrying": 0, "success_rate": None, "p50_ms": None, "p95_ms": None, "last_success": None, "last_failure": None, "state": "quiet"}


# --- configuration ----------------------------------------------------------------------------------------------------


@router.get("/push/config", response=PushConfigOut, summary="Provider credentials status and the editable push settings")
def get_config(request):
    row = PushSettings.get()
    return {"providers": cfg.providers_status(row), "settings": cfg.settings_dict(row), "sandbox_default": bool(getattr(settings, "PUSH_SANDBOX", False))}


@router.patch("/push/config", response=PushSettingsOut, summary="Change any subset of the push settings")
def patch_config(request, payload: PushSettingsIn):
    row, changed, errors = cfg.update(payload.dict(exclude_unset=True))
    if errors:
        raise ApiError(422, "validation_error", "The request was not valid.", errors)
    if changed:
        audit.record(request.user, "push.config_changed", target_type="pushsettings", target_id="1", data={"changed": changed})
    return cfg.settings_dict(row)


@router.post("/push/config/validate/{provider}", response=CredentialsCheckOut, summary="Check the credentials of one provider locally (nothing is sent)")
def validate_credentials(request, provider: str):
    from notifications import gateways

    if provider not in cfg.PROVIDERS:
        raise ApiError(404, "not_found", "Unknown provider.")
    try:
        result = gateways.validate_credentials(provider)
        out = {"ok": bool(result.get("ok")), "detail": str(result.get("detail", ""))}
    except Exception as e:  # a broken credential must be reported, not crash the page
        out = {"ok": False, "detail": f"{type(e).__name__}: {e}"[:300]}
    audit.record(request.user, "push.credentials_validated", target_type="pushprovider", target_id=provider, data={"provider": provider, "ok": out["ok"]})
    return out


# --- sandbox ----------------------------------------------------------------------------------------------------------


def _scenarios():
    return [{"value": v, "label": str(label), "description": SCENARIO_TEXT.get(v, "")} for v, label in SandboxState.Scenario.choices if v != "dead" or settings.DEBUG]


def _sandbox_out() -> dict:
    from notifications import sandbox

    events = SandboxEvent.objects.select_related("user").order_by("-created")[:20]
    return {
        "state": sandbox.get_state(),
        "scenarios": _scenarios(),
        "events": [{"id": e.pk, "created": e.created, "user_email": e.user.email if e.user_id else None, "action": e.action, "detail": e.detail} for e in events],
    }


@router.get("/push/sandbox", response=SandboxOut, summary="Sandbox state, scenarios and the latest events")
def get_sandbox(request):
    return _sandbox_out()


@router.put("/push/sandbox/mode", response=SandboxOut, summary="Switch the sandbox on (needs confirm) or off")
def put_sandbox_mode(request, payload: SandboxModeIn):
    from notifications import sandbox

    if payload.enabled and not payload.confirm:
        raise ApiError(422, "validation_error", "Confirm that all push will stop reaching real devices.", {"confirm": ["Required to switch the sandbox on."]})
    expires = payload.expires_in_minutes if payload.enabled else None
    sandbox.set_mode(request.user, payload.enabled, expires)
    audit.record(request.user, "push.sandbox_mode", target_type="sandbox", target_id="1", data={"enabled": payload.enabled, "expires_in_minutes": expires})
    return _sandbox_out()


@router.put("/push/sandbox/scenario", response=SandboxOut, summary="Choose what the fake provider answers, and how slowly")
def put_sandbox_scenario(request, payload: SandboxScenarioIn):
    from notifications import sandbox

    allowed = {s["value"] for s in _scenarios()}
    errors = {}
    if payload.scenario not in allowed:
        errors["scenario"] = ["Unknown scenario."]
    if not 0 <= payload.latency_ms <= 10000:
        errors["latency_ms"] = ["Must be between 0 and 10000."]
    if errors:
        raise ApiError(422, "validation_error", "The request was not valid.", errors)
    try:
        sandbox.set_scenario(request.user, payload.scenario, payload.latency_ms)
    except PermissionError as e:
        raise ApiError(409, "conflict", "Switch the sandbox on first.") from e
    except ValueError as e:
        raise ApiError(422, "validation_error", str(e), {"scenario": [str(e)]}) from e
    audit.record(request.user, "push.sandbox_scenario", target_type="sandbox", target_id="1", data={"scenario": payload.scenario, "latency_ms": payload.latency_ms})
    return _sandbox_out()


class MessagesQuery(Schema):
    channel: str | None = None
    provider: str | None = None
    outcome: str | None = None
    club: str | None = None


def _target_label(msg, devices: dict, subs: dict) -> str:
    if msg.channel == "web":
        sub = subs.get(msg.target)
        return f"Web push · {sub.user_agent[:40] or 'browser'}" if sub else "device removed"
    if msg.channel == "email":
        return "Email"
    dev = devices.get(msg.target)
    if dev is None:
        return "device removed"
    parts = [dev.get_platform_display(), dev.environment, f"v{dev.app_version}" if dev.app_version else "", "enabled" if dev.enabled else "disabled"]
    return " · ".join(p for p in parts if p)


@router.get("/push/sandbox/messages", response=SandboxMessagesOut, summary="What the sandbox captured (latest 100)")
def sandbox_messages(request, q: MessagesQuery = Query(...)):
    base = SandboxMessage.objects.all()
    club = mon.parse_club(q.club)
    if club is not None:
        base = base.filter(club=club)
    if q.channel:
        base = base.filter(channel=q.channel)
    if q.provider:
        base = base.filter(provider=q.provider)
    outcomes = dict(base.values_list("outcome").annotate(n=Count("id")).order_by())
    items = list((base.filter(outcome=q.outcome) if q.outcome else base).select_related("club", "user")[:100])
    targets = {m.target for m in items}
    devices = {d.installation_id: d for d in PushDevice.objects.filter(installation_id__in=targets)}
    subs = {s.endpoint: s for s in WebPushSubscription.objects.filter(endpoint__in=targets)}
    return {
        "items": [
            {
                "id": m.pk,
                "created": m.created,
                "channel": m.channel,
                "provider": m.provider,
                "club": _club(m.club) if m.club_id else None,
                "user_email": m.user.email if m.user_id else None,
                "target": mon.mask_email(m.target) if "@" in m.target else m.target,
                "target_label": _target_label(m, devices, subs),
                "title": m.title,
                "body": m.body,
                "request": m.request if isinstance(m.request, dict) else {"value": m.request},
                "outcome": m.outcome,
            }
            for m in items
        ],
        "outcomes": outcomes,
        "providers": sorted(SandboxMessage.objects.order_by().values_list("provider", flat=True).distinct()),
    }


@router.delete("/push/sandbox/messages", response=DeletedOut, summary="Empty the sandbox inbox")
def clear_sandbox_messages(request):
    from notifications import sandbox

    deleted = sandbox.clear_messages(request.user)
    audit.record(request.user, "push.sandbox_cleared", target_type="sandbox", target_id="1", data={"deleted": deleted})
    return {"deleted": deleted}


# --- composer ---------------------------------------------------------------------------------------------------------


@router.get("/push/targets", response=list[TargetOut], summary="Find an account to send a test to")
def targets(request, q: str = ""):
    User = get_user_model()
    terms = q.split()
    if not terms:
        return []
    qs = User.objects.all()
    for term in terms:
        qs = qs.filter(Q(email__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term))
    return [{"type": "user", "id": u.pk, "label": f"{u.email} ({u.first_name} {u.last_name})".replace(" ()", "").strip()} for u in qs.order_by("email")[:10]]


def _target(payload: SendIn):
    """(user, only, club, target description). 404 for an unknown target."""
    t = payload.target
    if t.type == "user":
        user = get_object_or_404(get_user_model(), pk=t.id)
        return user, None, None
    if t.type == "device":
        device = get_object_or_404(PushDevice.objects.select_related("user"), pk=t.id)
        return device.user, device, None
    sub = get_object_or_404(WebPushSubscription.objects.select_related("user", "club"), pk=t.id)
    return sub.user, sub, sub.club


@router.post("/push/send", response=SendOut, summary="Send a test notification to an account or one device, live or into the sandbox")
def send(request, payload: SendIn):
    from notifications import sandbox, services

    errors = {}
    if payload.category not in Category.values:
        errors["category"] = [f"Must be one of: {', '.join(Category.values)}."]
    if payload.data is not None and len(json.dumps(payload.data, separators=(",", ":"), ensure_ascii=False).encode()) > DATA_LIMIT_BYTES:
        errors["data"] = [f"At most {DATA_LIMIT_BYTES} bytes of JSON."]
    if payload.mode == "live" and not payload.confirm_live:
        errors["confirm_live"] = ["Confirm that this goes to real devices."]
    if errors:
        raise ApiError(422, "validation_error", "The request was not valid.", errors)
    user, only, club = _target(payload)
    results = services.send_now(
        user,
        title=payload.title,
        body=payload.body,
        category=payload.category,
        priority=payload.priority,
        data=payload.data,
        club=club,
        only=only,
        force_sandbox=payload.mode == "sandbox",
        respect_preferences=False,
    )
    sandboxed = payload.mode == "sandbox" or any(r.sandboxed for r in results) or (not results and sandbox.is_on())
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    audit.record(
        request.user,
        "push.test_sent",
        target=user,
        data={"target": {"type": payload.target.type, "id": str(payload.target.id)}, "mode": payload.mode, "sandboxed": sandboxed, "title": payload.title, "results": len(results), "counts": counts},
    )
    return {
        "mode": payload.mode,
        "sandboxed": sandboxed,
        "results": [{"channel": r.channel, "provider": r.provider, "target_masked": r.target_masked, "status": r.status, "error": r.error, "duration_ms": r.duration_ms} for r in results],
    }


# --- a user's push state ----------------------------------------------------------------------------------------------


def _user(user_id: UUID):
    return get_object_or_404(get_user_model(), pk=user_id)


def _preferences(user) -> dict:
    from notifications import preferences

    mode_row = UserNotificationSettings.objects.filter(user=user).first()
    mode = mode_row.email_mode if mode_row else UserNotificationSettings.EmailMode.ALWAYS
    quiet = QuietHours.objects.filter(user=user).first()
    quiet_out = None
    if quiet:
        active, until = preferences.quiet_state(user)
        quiet_out = {"enabled": quiet.enabled, "start": quiet.start.strftime("%H:%M"), "end": quiet.end.strftime("%H:%M"), "timezone": quiet.timezone, "active_now": bool(active) and quiet.enabled, "until": until if quiet.enabled else None}
    switches = MasterSwitch.objects.filter(user=user).select_related("club").order_by("channel", "club__name")
    off = NotificationPreference.objects.filter(user=user, enabled=False).select_related("club").order_by("category", "channel", "club__name")
    return {
        "email_mode": mode,
        "email_mode_label": str(UserNotificationSettings.EmailMode(mode).label),
        "quiet_hours": quiet_out,
        "master_switches": [{"club": _club(s.club) if s.club_id else None, "channel": s.channel, "enabled": s.enabled} for s in switches],
        "categories_off": [{"club": _club(p.club) if p.club_id else None, "category": p.category, "category_label": str(Category(p.category).label), "channel": p.channel} for p in off],
    }


@router.get("/users/{user_id}/push", response=UserPushOut, summary="An account's devices, web subscriptions, notification preferences and recent deliveries")
def user_push(request, user_id: UUID):
    user = _user(user_id)
    stale_before = timezone.now() - timedelta(days=PushSettings.get().stale_device_days)
    devices = PushDevice.objects.filter(user=user).order_by("-last_seen")
    subs = WebPushSubscription.objects.filter(user=user).select_related("club").order_by("-last_seen")
    recent = NotificationDelivery.objects.filter(user=user).order_by("-created")[:10]
    return {
        "devices": [
            {
                "id": d.pk,
                "platform": d.platform,
                "platform_label": d.get_platform_display(),
                "provider": d.provider,
                "environment": d.environment,
                "app_version": d.app_version,
                "locale": d.locale,
                "enabled": d.enabled,
                "disabled_at": d.disabled_at,
                "last_seen": d.last_seen,
                "stale": d.last_seen < stale_before,
            }
            for d in devices
        ],
        "web_subscriptions": [{"id": s.pk, "club": _club(s.club) if s.club_id else None, "user_agent": s.user_agent, "endpoint_masked": mon.truncate(s.endpoint), "last_seen": s.last_seen} for s in subs],
        "preferences": _preferences(user),
        "recent_deliveries": [{"id": d.pk, "created": d.created, "provider": d.provider, "status": d.status, "category": d.category, "title": d.title, "attempts": d.attempts, "error": d.last_error} for d in recent],
    }


@router.delete("/users/{user_id}/push/devices/{device_id}", response={204: None}, summary="Remove one of an account's devices")
def remove_device(request, user_id: UUID, device_id: UUID):
    user = _user(user_id)
    device = get_object_or_404(PushDevice, pk=device_id, user=user)
    data = {"device_id": str(device.pk), "platform": device.platform, "environment": device.environment, "installation_id": mon.truncate(device.installation_id), "app_version": device.app_version}
    device.delete()
    audit.record(request.user, "push.device_removed", target=user, data=data)
    return 204, None


@router.delete("/users/{user_id}/push/web-subscriptions/{sub_id}", response={204: None}, summary="Remove one of an account's web push subscriptions")
def remove_web_subscription(request, user_id: UUID, sub_id: UUID):
    user = _user(user_id)
    sub = get_object_or_404(WebPushSubscription, pk=sub_id, user=user)
    data = {"subscription_id": str(sub.pk), "endpoint": mon.truncate(sub.endpoint), "club_id": str(sub.club_id) if sub.club_id else None}
    sub.delete()
    audit.record(request.user, "push.web_subscription_removed", target=user, data=data)
    return 204, None
