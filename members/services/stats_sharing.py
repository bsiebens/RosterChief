"""The personal public link to one person's game stats (``/stats/<token>`` in the management app, answered by
``GET /api/v1/public/player-stats/<token>``). Opt-in per person: no token, no page. Members managers switch it on, replace
it (the old link stops working at once) or switch it off; every change is audited."""

import secrets

from django.db import transaction

from members.models import Member

from . import audit
from .access import require_admin


@transaction.atomic
def enable(actor, member: Member, *, rotate: bool = False) -> str:
    """The member's share token: created when missing, replaced with ``rotate``."""
    require_admin(actor)
    before = member.stats_share_token
    if before and not rotate:
        return before
    member.stats_share_token = secrets.token_urlsafe(24)
    member.save(update_fields=["stats_share_token", "modified"])
    audit.record(actor, "member.stats_share_enabled" if not before else "member.stats_share_rotated", member=member, target=member)
    return member.stats_share_token


@transaction.atomic
def disable(actor, member: Member) -> None:
    require_admin(actor)
    if member.stats_share_token:
        member.stats_share_token = None
        member.save(update_fields=["stats_share_token", "modified"])
        audit.record(actor, "member.stats_share_disabled", member=member, target=member)


def member_for_token(token: str) -> Member | None:
    return Member.objects.filter(stats_share_token=token).first() if token else None
