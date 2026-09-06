"""Resolving "which Member is this signed-in User", request-scoped.

Duplicated identically across mobile/mixins.py, mobile/coach_mixins.py, and
management/context_processors.py's notification_bell before this existed -- each
independently ran its own ``Member.objects.filter(user=request.user).first()`` within the
same request. ``Member.user`` is a plain OneToOneField (rosterchief.base's
ClubScopedModel doesn't apply -- Member is global, a person can belong to more than one
club), so there is at most one row to find regardless of which club is current.
"""

from django.http import HttpRequest

from members.models import Member
from rosterchief.request_cache import cached_on_request


def get_request_member(request: HttpRequest) -> Member | None:
    if not request.user.is_authenticated:
        return None
    return cached_on_request(request, "member", lambda: Member.objects.filter(user=request.user).first())
