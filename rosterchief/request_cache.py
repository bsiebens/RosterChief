"""Per-request memoization -- the same answer, wanted more than once within a single
request/response cycle by unrelated context processors, mixins, and views, computed once
and reused instead of re-derived with its own query every time.

Deliberately not a Django cache-framework cache: nothing here needs a TTL or a cross-request
invalidation story, and rides purely on ``request`` already being one shared, live-only-for-
this-request object every one of those call sites already has a reference to. See
club.services.access.get_current_season/get_club_admin and
members.services.lookup.get_request_member for what this actually backs.
"""

from collections.abc import Callable

_UNSET = object()


def cached_on_request[T](request, key: str, compute: Callable[[], T]) -> T:
    """Return ``compute()``, computed once per ``request`` and reused for every later
    call with the same ``key`` on that same request -- including a value of ``None``,
    which is why this doesn't just use ``dict.setdefault`` (that would call ``compute()``
    again on the next request-scoped miss, since a stored ``None`` looks the same as
    "never computed" to ``setdefault``)."""
    cache = request.__dict__.setdefault("_rosterchief_request_cache", {})
    cached = cache.get(key, _UNSET)
    if cached is _UNSET:
        cached = cache[key] = compute()
    return cached
