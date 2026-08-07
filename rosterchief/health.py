"""Liveness for the proxy today, for a load balancer later.

Checks the two dependencies whose absence makes the app lie rather than fail: without the
database it cannot serve anything, and without a shared cache the feature flags drift apart
between workers. A health check that only proves the process is listening would call that
healthy.
"""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

PROBE_KEY = "healthz"


@never_cache
def healthz(request):
    checks = {"database": _database(), "cache": _cache()}
    healthy = all(checks.values())

    return JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)


def _database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return False

    return True


def _cache() -> bool:
    """A round trip, not a ping: a cache that accepts writes and returns nothing is worse
    than one that is plainly down, because waffle would read every flag as unset."""
    try:
        cache.set(PROBE_KEY, "ok", 10)

        return cache.get(PROBE_KEY) == "ok"
    except Exception:
        return False
