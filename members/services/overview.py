"""Numbers for the management app's overview page: KPIs, signups per month, fee status, renewal rate and the
summary cards. Read-only; everything is about the current season (the one covering today).

Definitions (so the page and the member list agree):
* a *member this season* holds a non-cancelled club membership in the season;
* *unrostered* = active or pending members on no team's roster and on no team's staff this season (club staff are never unrostered);
* *new* = a member whose first ever membership is this season, *returning* = anyone with an earlier one;
* a team has *no coach* when nobody on its staff holds a management position this season;
* *renewal rate* = share of last season's (non-cancelled) members who are members again this season.
* *attendance* = turnout (present / (present + absent)) over this season's past events and how many past,
  non-cancelled events there were (``events.services.attendance_report.club_report``); null without past events.
"""

import datetime
from collections import Counter

from django.db.models import Count, Exists, Min, OuterRef
from django.utils import timezone

from events.models import Event, Season
from events.services.attendance_report import club_report
from members.models import ClubMembership, Member, MemberRequirementStatus
from teams.models import StaffAssignment, Team, TeamMembership

Status = ClubMembership.StatusChoices
FeeStatus = ClubMembership.FeeStatus


def _previous_season(season):
    return Season.objects.filter(end_date__lt=season.start_date).order_by("-end_date").first() if season else None


def _confirmed():
    """Memberships that count: not those of a registration whose invoice staff have not confirmed yet (see ``registration.services.invoicing``)."""
    from registration.services.invoicing import awaiting_membership_ids

    return ClubMembership.objects.exclude(pk__in=awaiting_membership_ids())


def _members_in(season):
    return _confirmed().filter(season=season).exclude(status=Status.CANCELLED)


def _month_starts(today: datetime.date, n: int = 12) -> list[datetime.date]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(datetime.date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def signups_per_month(season, today: datetime.date) -> list[dict]:
    """Last 12 months of sign-ups (by ``signed_up_at``), split into new members and returning ones."""
    months = _month_starts(today)
    first_season = dict(_confirmed().values("member").annotate(first=Min("season__start_date")).values_list("member", "first"))
    rows = _confirmed().filter(signed_up_at__gte=months[0]).exclude(status=Status.CANCELLED).values_list("member_id", "signed_up_at", "season__start_date")
    counts: dict = {m: Counter() for m in months}
    for member_id, signed, start in rows:
        key = datetime.date(signed.year, signed.month, 1)
        if key in counts:
            counts[key]["new" if first_season.get(member_id) == start else "returning"] += 1
    return [{"month": m.strftime("%Y-%m"), "new": counts[m]["new"], "returning": counts[m]["returning"]} for m in months]


def fee_status_counts(season) -> dict:
    qs = _members_in(season).values("fee_status").annotate(n=Count("id"))
    got = {r["fee_status"]: r["n"] for r in qs}
    return {s.value: got.get(s.value, 0) for s in FeeStatus}


def _renewal_rate(season, previous) -> tuple[int, int] | None:
    """``(renewed, of)`` between two seasons, or ``None`` without data."""
    if season is None or previous is None:
        return None
    before = set(_members_in(previous).values_list("member_id", flat=True))
    if not before:
        return None
    return len(before & set(_members_in(season).values_list("member_id", flat=True))), len(before)


def renewal(season, previous) -> dict | None:
    """This season's renewal, plus ``previous_rate``: the same figure one season earlier, to compare against."""
    got = _renewal_rate(season, previous)
    if got is None:
        return None
    renewed, of = got
    before = _renewal_rate(previous, _previous_season(previous))
    return {
        "previous_season": previous.name,
        "renewed": renewed,
        "of": of,
        "rate": round(renewed * 100 / of),
        "previous_rate": round(before[0] * 100 / before[1]) if before else None,
    }


def _new_members(season) -> int:
    """Members whose first ever membership is ``season``."""
    earlier = _confirmed().filter(member=OuterRef("member"), season__start_date__lt=season.start_date)
    return _members_in(season).exclude(Exists(earlier)).count()


def admin_count() -> int:
    """Users of this club holding at least one admin-flagged permission through a role (the Owner role included)."""
    from tenant_users.permissions.models import UserTenantPermissions

    from authorization.catalog import ADMIN_CODES

    names = [c.split(".", 1)[1] for c in ADMIN_CODES]
    return UserTenantPermissions.objects.filter(groups__permissions__codename__in=names).distinct().count()


def overview(*, include_fees: bool, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    season = Season.get_current(today)
    previous = _previous_season(season)
    months = signups_per_month(season, today) if season else []
    if season is None:
        return {"season": None, "previous_season": None, "kpis": None, "signups": months, "fees": None, "renewal": None, "cards": None, "attendance": None}

    members = _members_in(season)
    open_ms = members.filter(status__in=[Status.ACTIVE, Status.PENDING])
    on_roster = TeamMembership.objects.filter(member=OuterRef("member"), season=season)
    on_staff = StaffAssignment.objects.filter(member=OuterRef("member"), season=season)
    coaches = StaffAssignment.objects.filter(team=OuterRef("pk"), season=season, position__management_position=True)

    total = members.count()
    # A previous season without any members is nothing to compare with.
    previous_total = _members_in(previous).count() if previous else 0
    kpis = {
        "total_members": total,
        "previous_total_members": previous_total or None,
        "awaiting_approval": members.filter(status=Status.PENDING).count(),
        "documents_to_review": MemberRequirementStatus.objects.filter(is_pending_review=True, membership__season=season).count() if include_fees else 0,
        "teams_without_coach": Team.objects.exclude(Exists(coaches)).count(),
        "unrostered_members": open_ms.exclude(Exists(on_roster) | Exists(on_staff)).exclude(member__club_staff=True).count(),
        "new_members": _new_members(season),
        "previous_new_members": _new_members(previous) if previous_total else None,
    }

    now = timezone.now()
    cards = {
        "people": {
            "admins": admin_count(),
            "staff": Member.objects.staff(season).count(),  # team staff of the season plus club staff
            "members": members.values("member").distinct().count(),
            "guardians": Member.objects.guardians().count(),
        },
        "members": {
            "total": total,
            "active": members.filter(status=Status.ACTIVE).count(),
            "pending": members.filter(status=Status.PENDING).count(),
            "lapsed": members.filter(status=Status.LAPSED).count(),
        },
        "teams": {
            "teams": Team.objects.count(),
            "players": TeamMembership.objects.filter(season=season).values("member").distinct().count(),
            "staff": StaffAssignment.objects.filter(season=season).values("member").distinct().count(),
        },
        "events": {
            "upcoming": Event.objects.filter(cancelled=False, start__gte=now).count(),
            "this_season": Event.objects.filter(cancelled=False, season=season).count(),
        },
    }
    return {
        "season": {"id": season.pk, "name": season.name},
        "previous_season": {"id": previous.pk, "name": previous.name} if previous else None,
        "kpis": kpis,
        "signups": months,
        "fees": fee_status_counts(season) if include_fees else None,
        "renewal": renewal(season, previous) if include_fees else None,
        "cards": cards,
        "attendance": club_report(season),
    }
