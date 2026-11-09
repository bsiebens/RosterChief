"""The sign-up queue: pending season memberships waiting for an admin to approve them.

A pending membership is *clean* when its fee is paid or waived and every active onboarding requirement is resolved. Paying never
activates by itself: approving is the one deliberate step (``memberships.activate``). Everything needs ``members.manage_members``.
Ported from the old ``club.services.onboarding`` (``is_signup_clean`` / ``approve_one`` / ``approve_all_clean``) and ``cancellation``.

Not ported: linking an anonymous sign-up to an existing member (the old ``signup_linking``). It only matters once public
registration creates anonymous members; it is part of that work, not of the queue.
"""

import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from events.models import Season
from members.models import ClubMembership, FamilyMembership, Member
from teams.models import Position, Team, TeamMembership

from . import audit, onboarding
from . import members as member_services
from . import memberships as fees
from .access import require_admin
from .errors import Invalid

Status = ClubMembership.StatusChoices
FeeStatus = ClubMembership.FeeStatus


def current_season() -> Season | None:
    return Season.get_current()


def pending(season: Season | None):
    """Pending memberships of ``season`` (the current one by default), newest sign-up first."""
    season = season or current_season()
    if season is None:
        return ClubMembership.objects.none()
    return ClubMembership.objects.filter(season=season, status=Status.PENDING).select_related("member", "season").order_by("-signed_up_at", "member__last_name", "member__first_name")


def is_clean(membership: ClubMembership) -> bool:
    """Fee paid or waived, and nothing in the onboarding checklist still open."""
    fees.sync_fee_status(membership)
    return membership.fee_status in (FeeStatus.PAID, FeeStatus.WAIVED) and not onboarding.unresolved(membership)


def describe(membership: ClubMembership) -> dict:
    """One queue row: the member, the money, what is still open and where they are already rostered this season."""
    fees.sync_fee_status(membership)
    open_items = onboarding.unresolved(membership)
    settled = membership.fee_status in (FeeStatus.PAID, FeeStatus.WAIVED)
    teams = list(TeamMembership.objects.filter(member=membership.member, season=membership.season).select_related("team").order_by("team__name"))
    return {
        "membership": membership,
        "open_requirements": [r.name for r in open_items],
        "clean": settled and not open_items,
        "remaining_balance": fees.remaining_balance(membership),
        "teams": [t.team for t in teams],
        "has_login": bool(membership.member.user_id),
    }


def rows(season: Season | None, *, q: str = "", only: str = "all") -> list[dict]:
    """The queue as ``describe`` rows, filtered by a name / email search and by ``only`` (``all`` | ``clean`` | ``open``)."""
    qs = pending(season)
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(member__first_name__icontains=q) | Q(member__last_name__icontains=q) | Q(member__email__icontains=q))
    out = [describe(m) for m in qs]
    if only == "clean":
        out = [r for r in out if r["clean"]]
    elif only == "open":
        out = [r for r in out if not r["clean"]]
    return out


def summary(season: Season | None) -> dict:
    """Counts for the nav badge and the header: everything pending, and how many of it still needs something."""
    items = [describe(m) for m in pending(season)]
    clean = sum(1 for r in items if r["clean"])
    return {"pending": len(items), "clean": clean, "not_clean": len(items) - clean}


# --- actions -------------------------------------------------------------------------------------------------------


def approve(actor, membership: ClubMembership) -> ClubMembership:
    """Activate one pending membership (the fee and requirement rules are ``memberships.activate``'s)."""
    require_admin(actor)
    if membership.status != Status.PENDING:
        raise Invalid("Only a pending sign-up can be approved.", {"__all__": ["This sign-up is not pending."]})
    return fees.activate(actor, membership)


def approve_all_clean(actor, season: Season | None) -> int:
    """Activate every clean pending membership of ``season``; returns how many. Each one is its own transaction, so a failure
    in one never blocks the others."""
    require_admin(actor)
    count = 0
    for membership in pending(season):
        if not is_clean(membership):
            continue
        try:
            fees.activate(actor, membership)
        except Invalid:
            continue
        count += 1
    return count


@transaction.atomic
def place_in_team(actor, membership: ClubMembership, team: Team, *, position_id=None, jersey_number: int | None = None) -> TeamMembership:
    """Put the person on ``team`` for the membership's season, whatever their status (a pending member can be provisionally
    rostered; ``blocked_event_kinds`` then decides what they may still be invited to). Admin-level: the team-manager check of
    the roster endpoint does not apply here."""
    require_admin(actor)
    from teams.services import teams as team_services

    member, season = membership.member, membership.season
    if TeamMembership.objects.filter(team=team, season=season, member=member).exists():
        raise Invalid(f"{member} is already on {team.name} in {season.name}.", {"team_id": ["Already on this team."]})
    position = team_services._position(position_id, staff=False)
    team_services._check_number(team, season, member, jersey_number)
    spot = TeamMembership.objects.create(team=team, member=member, season=season, position=position, jersey_number=jersey_number)
    audit.record(actor, "team.roster_added", member=member, target=spot, target_label=f"{member} - {team.name}", changes={k: {"from": None, "to": v} for k, v in team_services._roster_state(spot).items()})
    return spot


def _has_other_ties(membership: ClubMembership) -> bool:
    """Whether the person is more than this one sign-up: another season, a family, a login, a roster spot or a payment."""
    member = membership.member
    return (
        ClubMembership.objects.filter(member=member).exclude(pk=membership.pk).exists()
        or FamilyMembership.objects.filter(member=member).exists()
        or bool(member.user_id)
        or TeamMembership.objects.filter(member=member).exists()
        or membership.payments.exists()
    )


@transaction.atomic
def cancel(actor, membership: ClubMembership) -> str:
    """Turn down a pending sign-up. A person who is nothing but this unpaid sign-up (no other season, family, login, team spot or
    payment) is removed entirely; anyone else keeps their record and the membership is cancelled (payments stay).
    Returns ``"deleted"`` or ``"cancelled"``."""
    require_admin(actor)
    if membership.status != Status.PENDING:
        raise Invalid("Only a pending sign-up can be cancelled here.", {"__all__": ["This sign-up is not pending."]})
    if fees.can_delete_membership(membership) and not _has_other_ties(membership):
        member_services.delete_member(actor, membership.member)
        return "deleted"
    fees.cancel(actor, membership)
    return "cancelled"


# --- admin add-member: the membership part --------------------------------------------------------------------------


@transaction.atomic
def enrol(actor, member: Member, season: Season, *, status: str = Status.ACTIVE, fee_amount=0, waived: bool = False, signed_up_at: datetime.date | None = None) -> ClubMembership:
    """Sign a person up for ``season`` as part of adding them by hand. ``active`` (the old default for staff-added people) skips
    the queue: activated on the spot, no fee or checklist required. ``pending`` sends them to the queue with ``fee_amount``
    (or ``waived``) like any other sign-up."""
    require_admin(actor)
    if status not in (Status.ACTIVE, Status.PENDING):
        raise Invalid("A new membership is either active or pending.", {"status": ["Invalid choice."]})
    membership = fees.create_membership(actor, member, season, fee_amount=fee_amount, status=Status.PENDING, signed_up_at=signed_up_at or timezone.localdate())
    if waived:
        fees.set_fee_waived(actor, membership, True)
    if status == Status.ACTIVE:
        membership.status = Status.ACTIVE
        membership.activated_at = timezone.localdate()
        membership.save(update_fields=["status", "activated_at", "modified"])
        audit.record(actor, "membership.activated", member=member, target=membership, changes={"status": {"from": Status.PENDING, "to": Status.ACTIVE}}, target_label=f"{season} - {member}")
    return membership


def positions_for_players() -> list[Position]:
    return list(Position.objects.filter(staff_position=False))
