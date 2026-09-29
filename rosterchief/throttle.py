"""Fixed-window submission counters kept in the Django cache.

Used to cap how often a public, unauthenticated form (registration, the
marketing contact form) can be submitted per client IP and per contact email.
Each ``Rate`` names its own scope, limit and window; callers keep their limits
as module constants next to the view that applies them.

The counters are best-effort: two requests racing past the check at the same
moment can overshoot a limit by one or two, which is acceptable for abuse
throttling. With the locmem cache (dev/tests) the counters are per process.
"""

import hashlib
from dataclasses import dataclass

from django.core.cache import cache


@dataclass(frozen=True)
class Rate:
    scope: str
    limit: int
    window: int  # seconds


def _cache_key(rate: Rate, identifier: str) -> str:
    # Hashed so arbitrary identifiers (emails, IPv6 addresses) always make a
    # valid, fixed-length cache key.
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return f"throttle:{rate.scope}:{digest}"


def is_limited(rate: Rate, identifier: str) -> bool:
    return cache.get(_cache_key(rate, identifier), 0) >= rate.limit


def record(rate: Rate, identifier: str) -> None:
    key = _cache_key(rate, identifier)
    # add() starts a new window (and its expiry); incr() keeps the existing one.
    if cache.add(key, 1, rate.window):
        return
    try:
        cache.incr(key)
    except ValueError:
        # Expired between add() and incr() -- start a fresh window.
        cache.set(key, 1, rate.window)


def check_and_record(*checks: tuple[Rate, str | None]) -> bool:
    """Return True when any ``(rate, identifier)`` pair is already at its
    limit (nothing is recorded then); otherwise count one hit against every
    pair and return False. Pairs with an empty identifier are skipped."""
    checks = tuple((rate, identifier) for rate, identifier in checks if identifier)
    if any(is_limited(rate, identifier) for rate, identifier in checks):
        return True
    for rate, identifier in checks:
        record(rate, identifier)
    return False
