"""Short-TTL, per-club caching for the public read-only API.

Every route under /api/v1/ shares one URL path across every club subdomain (see
api/urls.py's own docstring) -- a naive per-path cache (Django's cache_page, or an
upstream HTTP cache keyed on the path alone) would serve one club's news/roster/
fixtures to another's external website. Every key built here embeds the resolved
club's own pk to rule that out, the same way api/errors.require_club resolves the
tenant before any route logic runs at all.

No event-driven invalidation: there's no existing signal-to-cache-bust wiring for
the several models each endpoint reads (News, Team, TeamMembership, Event, ...),
and building one is a bigger first step than this phase's other caching additions.
A short, fixed TTL instead -- external sites polling these endpoints get an answer
that is at most CACHE_SECONDS stale, never indefinitely so, and every value cached
here is either a plain dict/list or a Ninja Schema instance built for one specific
request, both of which round-trip through Django's cache backend (pickled) exactly
like the JSON they end up serialised to.
"""

from django.core.cache import cache

#: Deliberately short: this is the highest-reward, highest-risk item in the
#: hardening plan's caching phase specifically because of the shared-path
#: risk above -- start conservative, revisit once this has run for a while.
CACHE_SECONDS = 30


def cached_for_club(club_id, key: str, compute):
    """Return ``compute()``, cached under a key scoped to both ``club_id`` and
    ``key`` (the endpoint's own name plus whatever parameters affect its
    result -- limit/offset, a slug, a team id, ...) for CACHE_SECONDS.
    ``compute`` raising (e.g. Ninja's HttpError for a 404) is never cached --
    only an actual result is worth serving again."""
    cache_key = f"api:club:{club_id}:{key}"
    value = cache.get(cache_key)
    if value is None:
        value = compute()
        cache.set(cache_key, value, CACHE_SECONDS)
    return value
