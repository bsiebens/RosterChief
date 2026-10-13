"""The sending-side sandbox: providers are replaced by a capture, switchable at runtime.

When on, nothing leaves the server: each push and notification email is stored as a ``SandboxMessage``
with the exact request the provider would have received, and a **failure scenario** decides what the
"provider" answers -- ``ok``, ``throttle`` (429), ``dead`` (the target is gone), ``error`` (refused, not
retryable) or ``timeout`` -- optionally after an artificial latency. The real code paths run unchanged
around it (delivery log, retries, held pushes, monitoring, alerts), which is the point. It says nothing
about the receiving end.

The switch lives in the database (``SandboxState``), flipped on the control panel, so it works in
production too. Safeguards: every change is audited (``SandboxEvent``), it can switch itself back to live
(``expires_at``), the control panel shows a banner while it is on, and the health check alerts the admins.
``settings.PUSH_SANDBOX`` is only the default while nobody has touched the switch.
Only the notification pipeline is sandboxed (pushes and notification mail); account mail (password
reset, verification) and invoices stay real.
"""

import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import SandboxEvent, SandboxMessage, SandboxState

Outcome = SandboxMessage.Outcome

CACHE_KEY = "push-sandbox-state"
#: Seconds a worker keeps its copy of the switch: every push asks, the database should not hear about it each time.
CACHE_SECONDS = 5
SCENARIOS = {
    "ok": Outcome.DELIVERED,
    "throttle": Outcome.THROTTLED,
    "dead": Outcome.DEAD,
    "error": Outcome.ERROR,
    "timeout": Outcome.TIMEOUT,
}
MAX_LATENCY_MS = 10_000
RETENTION_DAYS = 7


def _snapshot() -> dict:
    """The stored switch as plain data (cached briefly; the cache is dropped on every write)."""
    snapshot = cache.get(CACHE_KEY)
    if snapshot is None:
        state = SandboxState.objects.filter(pk=1).select_related("changed_by").first()
        snapshot = {
            "enabled": state.enabled if state else None,
            "scenario": state.scenario if state else "ok",
            "latency_ms": state.latency_ms if state else 0,
            "expires_at": state.expires_at if state else None,
            "changed_by": (state.changed_by.get_username() if state and state.changed_by else ""),
            "changed_at": state.changed_at if state else None,
        }
        cache.set(CACHE_KEY, snapshot, CACHE_SECONDS)
    return snapshot


def enabled(now=None) -> bool:
    """Whether pushes/notification mail are captured instead of sent. A switch that was turned on with an
    expiry is off again once that has passed, even before ``expire_if_due`` records it."""
    snapshot = _snapshot()
    if snapshot["enabled"] is None:
        return bool(getattr(settings, "PUSH_SANDBOX", False))
    if snapshot["enabled"] and snapshot["expires_at"] and snapshot["expires_at"] <= (now or timezone.now()):
        return False
    return snapshot["enabled"]


def scenario() -> dict:
    """``{"name": "ok", "latency_ms": 0}`` -- what the sandbox "provider" currently does."""
    snapshot = _snapshot()
    name = snapshot["scenario"] if snapshot["scenario"] in SCENARIOS else "ok"
    return {"name": name, "latency_ms": int(snapshot["latency_ms"] or 0)}


def status(now=None) -> dict:
    """Everything the control panel shows about the switch."""
    snapshot = _snapshot()
    return {
        "enabled": enabled(now),
        "set_explicitly": snapshot["enabled"] is not None,
        "default_from_setting": bool(getattr(settings, "PUSH_SANDBOX", False)),
        "expires_at": snapshot["expires_at"] if snapshot["enabled"] else None,
        "changed_by": snapshot["changed_by"],
        "changed_at": snapshot["changed_at"],
        **scenario(),
    }


def _save(user, action: str, detail: str = "", **fields) -> SandboxState:
    state, _created = SandboxState.objects.get_or_create(pk=1)
    for name, value in fields.items():
        setattr(state, name, value)
    state.changed_by = user if getattr(user, "pk", None) else None
    state.save()
    SandboxEvent.objects.create(user=state.changed_by, action=action, detail=detail[:255])
    cache.delete(CACHE_KEY)
    return state


def set_mode(user, *, on: bool, expires_in: timedelta | None = None) -> SandboxState:
    """Switch between sandbox (``on``) and live. ``expires_in`` makes a sandbox period end on its own."""
    expires_at = timezone.now() + expires_in if (on and expires_in) else None
    detail = f"until {expires_at:%Y-%m-%d %H:%M}" if expires_at else ""
    return _save(user, "sandbox_on" if on else "live", detail, enabled=on, expires_at=expires_at)


def set_scenario(name: str, latency_ms: int = 0, user=None) -> dict:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    latency = max(0, min(int(latency_ms), MAX_LATENCY_MS))
    _save(user, "scenario", f"{name}, {latency} ms", scenario=name, latency_ms=latency)
    return scenario()


def expire_if_due(now=None) -> bool:
    """Record an expired sandbox period: back to live for good. Returns whether it just expired."""
    now = now or timezone.now()
    state = SandboxState.objects.filter(pk=1, enabled=True, expires_at__lte=now).first()
    if state is None:
        return False
    _save(None, "expired", "back to live", enabled=False, expires_at=None)
    return True


def capture(*, channel: str, provider: str, target, title: str = "", body: str = "", request=None, headers=None, user=None, club=None) -> str:
    """Store the message and return the ``Outcome`` the current scenario dictates (after its latency).
    The caller maps the outcome onto what the real provider call would have done (return, return False,
    raise a retryable or a plain error)."""
    current = scenario()
    if current["latency_ms"]:
        time.sleep(current["latency_ms"] / 1000)
    outcome = SCENARIOS[current["name"]]
    SandboxMessage.objects.create(
        channel=channel,
        provider=provider,
        target=str(target)[:255],
        user=user,
        club=club,
        title=str(title)[:255],
        body=str(body),
        request=request or {},
        headers=headers or {},
        outcome=outcome,
    )
    return outcome


def purge(now=None) -> int:
    from datetime import timedelta

    from django.utils import timezone

    now = now or timezone.now()
    count, _deleted = SandboxMessage.objects.filter(created__lt=now - timedelta(days=RETENTION_DAYS)).delete()
    return count
