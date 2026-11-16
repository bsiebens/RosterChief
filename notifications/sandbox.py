"""The sending sandbox: while on, every provider is replaced by a fake one that captures the exact request as a ``SandboxMessage``
instead of sending it, and answers as the active scenario dictates (ok, throttle, dead, error, timeout) after an optional latency.
The real code paths around it (delivery log, retries, held pushes, monitoring, alerts) run unchanged.

The switch lives in the database (``SandboxState``), so it works in production too. Every change is audited (``SandboxEvent``), it can
switch itself back to live (``expires_at``), and the health check warns the admins when it is left on. ``settings.PUSH_SANDBOX`` is
only the default while nobody touched the switch."""

import time
from datetime import timedelta

from decouple import config
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import SandboxEvent, SandboxMessage, SandboxState

CACHE_KEY = "notifications:sandbox-state"
CACHE_SECONDS = 5
ALLOWED_EXPIRIES = (30, 60, 240, 1440, None)
MAX_LATENCY_MS = 10_000
RETENTION_DAYS = 7

Scenario = SandboxState.Scenario
#: The scenario's name -> the outcome stored on the captured message.
OUTCOMES = {Scenario.OK: "delivered", Scenario.THROTTLE: "throttled", Scenario.DEAD: "dead", Scenario.ERROR: "error", Scenario.TIMEOUT: "timeout"}
#: Scenarios with side effects on real data: only while DEBUG is on ("dead" disables the devices it hits, like a real 410).
DEBUG_ONLY_SCENARIOS = {Scenario.DEAD}


def default_from_settings() -> bool:
    value = getattr(settings, "PUSH_SANDBOX", None)
    return bool(config("PUSH_SANDBOX", default=False, cast=bool) if value is None else value)


def _snapshot() -> dict:
    snapshot = cache.get(CACHE_KEY)
    if snapshot is None:
        state = SandboxState.objects.filter(pk=1).first()
        snapshot = {
            "enabled": state.enabled if state else None,
            "scenario": state.scenario if state else Scenario.OK,
            "latency_ms": state.latency_ms if state else 0,
            "expires_at": state.expires_at if state else None,
        }
        cache.set(CACHE_KEY, snapshot, CACHE_SECONDS)
    return snapshot


def _effective(snapshot: dict, now) -> bool:
    if snapshot["enabled"] is None:
        return default_from_settings()
    if snapshot["enabled"] and snapshot["expires_at"] and snapshot["expires_at"] <= now:
        return False
    return bool(snapshot["enabled"])


def is_on(now=None) -> bool:
    """Whether sends are captured instead of delivered (cached ~5 s; an expired period counts as off immediately)."""
    return _effective(_snapshot(), now or timezone.now())


def available_scenarios() -> list[str]:
    return [s.value for s in Scenario if settings.DEBUG or s not in DEBUG_ONLY_SCENARIOS]


def current_scenario() -> tuple[str, int]:
    """``(name, latency_ms)``. A stored debug-only scenario counts as ok when DEBUG is off (e.g. a database copied from a dev machine)."""
    snapshot = _snapshot()
    name = snapshot["scenario"] if snapshot["scenario"] in available_scenarios() else Scenario.OK.value
    return name, int(snapshot["latency_ms"] or 0)


def get_state(now=None) -> dict:
    now = now or timezone.now()
    snapshot = _snapshot()
    name, latency = current_scenario()
    return {
        "enabled": snapshot["enabled"],
        "effective": _effective(snapshot, now),
        "expires_at": snapshot["expires_at"] if snapshot["enabled"] else None,
        "scenario": name,
        "latency_ms": latency,
        "default_from_settings": default_from_settings(),
    }


def _save(user, action: str, detail: str = "", **fields) -> SandboxState:
    state, _created = SandboxState.objects.get_or_create(pk=1)
    for name, value in fields.items():
        setattr(state, name, value)
    state.save()
    SandboxEvent.objects.create(user=user if getattr(user, "pk", None) else None, action=action, detail=detail[:300])
    cache.delete(CACHE_KEY)
    return state


def set_mode(user, enabled: bool, expires_in_minutes: int | None = None) -> SandboxState:
    if expires_in_minutes not in ALLOWED_EXPIRIES:
        raise ValueError("expires_in_minutes must be 30, 60, 240, 1440 or None")
    expires_at = timezone.now() + timedelta(minutes=expires_in_minutes) if (enabled and expires_in_minutes) else None
    detail = f"until {timezone.localtime(expires_at):%Y-%m-%d %H:%M}" if expires_at else ""
    return _save(user, "sandbox_on" if enabled else "sandbox_off", detail, enabled=enabled, expires_at=expires_at)


def set_scenario(user, scenario: str, latency_ms: int) -> SandboxState:
    if scenario not in available_scenarios():
        raise ValueError(f"unknown scenario: {scenario}")
    if not 0 <= int(latency_ms) <= MAX_LATENCY_MS:
        raise ValueError(f"latency must be between 0 and {MAX_LATENCY_MS} ms")
    if not is_on():
        raise PermissionError("Switch the sandbox on first.")
    return _save(user, "scenario", f"{scenario}, {int(latency_ms)} ms", scenario=scenario, latency_ms=int(latency_ms))


def clear_messages(user) -> int:
    count, _deleted = SandboxMessage.objects.all().delete()
    SandboxEvent.objects.create(user=user if getattr(user, "pk", None) else None, action="cleared", detail=f"{count} messages")
    return count


def expire_if_due(now=None) -> bool:
    """Record a sandbox period that ran out: back to live for good. Returns whether it just expired."""
    now = now or timezone.now()
    if not SandboxState.objects.filter(pk=1, enabled=True, expires_at__lte=now).exists():
        return False
    _save(None, "expired", "back to live", enabled=False, expires_at=None)
    return True


def capture(*, channel: str, provider: str, target, title: str = "", body: str = "", request=None, user=None, club=None) -> str:
    """Store the message and return its outcome (delivered/throttled/dead/error/timeout), after the scenario's latency."""
    name, latency = current_scenario()
    if latency:
        time.sleep(min(latency, MAX_LATENCY_MS) / 1000)
    outcome = OUTCOMES[Scenario(name)]
    SandboxMessage.objects.create(channel=channel, provider=provider, target=str(target)[:500], user=user, club=club, title=str(title)[:255], body=str(body), request=request or {}, outcome=outcome)
    return outcome


def purge(now=None) -> int:
    now = now or timezone.now()
    count, _deleted = SandboxMessage.objects.filter(created__lt=now - timedelta(days=RETENTION_DAYS)).delete()
    return count
