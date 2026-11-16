"""Platform dashboard statistics.

Club data lives in one schema per club, so there is no single cross-club query. A snapshot loops over the clubs once,
gathers per-club facts (running registered *collectors* inside each club's schema), and derives every metric from
those facts plus the shared tables. Apps add their own numbers later with ``@collector`` (per-club data) and
``@metric`` (a platform number); nothing here needs to change when new domain apps arrive.

The snapshot is cached for two minutes (``snapshot(refresh=True)`` bypasses it).
"""

import datetime
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

CACHE_KEY = "controlpanel:dashboard_snapshot"
CACHE_SECONDS = 120
AT_LIMIT_RATIO = 0.9  # a club is "near its member limit" from this share of the limit

_collectors: dict[str, Callable[[], Any]] = {}
_metrics: list[Metric] = []


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str  # "totals" or "attention"
    compute: Callable[[Snapshot], Any]


def collector(key: str):
    """Register a function run *inside each club's schema*; its return value lands in ``ClubFacts.data[key]``."""

    def decorator(fn):
        _collectors[key] = fn
        return fn

    return decorator


def metric(key: str, label: str, *, group: str = "totals"):
    """Register a platform metric computed from the snapshot's per-club facts and shared tables."""

    def decorator(fn):
        _metrics.append(Metric(key, label, group, fn))
        return fn

    return decorator


@dataclass
class ClubFacts:
    club: Any
    entitlements: Any
    plan: str | None
    billed: bool
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def members(self) -> int:
        return self.data.get("members", 0)

    @property
    def member_limit(self) -> int | None:
        return self.entitlements.limit("max_members") if self.entitlements.has("max_members") else 0


@dataclass
class Snapshot:
    clubs: list[ClubFacts]  # active clubs only
    archived: int
    today: datetime.date


# --- built-in collectors (run inside a club schema) -----------------------------------------------------------------


@collector("members")
def _members() -> int:
    from members.models import Member

    return Member.objects.count()


@collector("events")
def _events() -> dict:
    from events.models import Event

    now = timezone.now()
    live = Event.objects.filter(cancelled=False)
    return {"upcoming": live.filter(start__gte=now).count(), "next_7d": live.filter(start__gte=now, start__lt=now + datetime.timedelta(days=7)).count()}


@collector("admin_ids")
def _admin_ids() -> list[str]:
    from authorization.catalog import OWNER_SLUG
    from authorization.models import Role

    group = Role.objects.get(slug=OWNER_SLUG).group
    return [str(pk) for pk in group.user_set.values_list("profile_id", flat=True)]


# --- facts ----------------------------------------------------------------------------------------------------------


def _gather() -> Snapshot:
    from billing.services.dues import current_plan
    from club.models import Club
    from features.entitlements import resolve_entitlements

    clubs = Club.objects.exclude(schema_name=get_public_schema_name())
    billed_ids = set(Club.objects.filter(billing__isnull=False).values_list("pk", flat=True))
    facts, archived = [], 0
    for club in clubs:
        if club.is_archived:
            archived += 1
            continue
        plan = current_plan(club)
        f = ClubFacts(club=club, entitlements=resolve_entitlements(club), plan=plan.code if plan else None, billed=club.pk in billed_ids)
        with schema_context(club.schema_name):
            for key, fn in _collectors.items():
                try:
                    f.data[key] = fn()
                except Exception:
                    f.data[key] = None
        facts.append(f)
    return Snapshot(clubs=facts, archived=archived, today=timezone.localdate())


# --- built-in metrics -----------------------------------------------------------------------------------------------


@metric("clubs_active", "Active clubs")
def _clubs_active(s):
    return len(s.clubs)


@metric("clubs_archived", "Archived clubs")
def _clubs_archived(s):
    return s.archived


@metric("members_total", "Members across all clubs")
def _members_total(s):
    return sum(f.members or 0 for f in s.clubs)


@metric("events_upcoming", "Upcoming events across all clubs")
def _events_upcoming(s):
    return sum((f.data.get("events") or {}).get("upcoming", 0) for f in s.clubs)


@metric("club_admins", "Club administrators (distinct people)")
def _club_admins(s):
    return len({uid for f in s.clubs for uid in (f.data.get("admin_ids") or [])})


@metric("clubs_unbilled", "Active clubs not being billed", group="attention")
def _clubs_unbilled(s):
    return sum(1 for f in s.clubs if not f.billed)


@metric("dues_owed", "Owed to the platform (EUR)", group="attention")
def _dues_owed(s):
    from billing.services.dues import owing_dues

    agg = owing_dues().aggregate(total=Sum("amount"), vat=Sum("vat_amount"), paid=Sum("amount_paid"))
    return str((agg["total"] or Decimal("0")) + (agg["vat"] or Decimal("0")) - (agg["paid"] or Decimal("0")))


@metric("dues_in_grace", "Periods in their grace period", group="attention")
def _dues_in_grace(s):
    from billing.services.dues import dues_in_grace

    return dues_in_grace(s.today).count()


@metric("dues_overdue", "Periods overdue past grace", group="attention")
def _dues_overdue(s):
    from billing.services.dues import dues_overdue

    return dues_overdue(s.today).count()


@metric("clubs_downgraded", "Clubs on the free plan because of unpaid periods", group="attention")
def _clubs_downgraded(s):
    from billing.models import ClubBilling

    return ClubBilling.objects.filter(downgraded_at__isnull=False, club__archived_at__isnull=True).count()


@metric("lapsed_clubs", "Billed clubs whose last period has ended with no new one", group="attention")
def _lapsed_clubs(s):
    from billing.services.dues import lapsed_billings

    return lapsed_billings(s.today).count()


@metric("invoices_to_send", "Invoices not sent yet", group="attention")
def _invoices_to_send(s):
    from billing.models import Invoice

    return Invoice.objects.filter(sent_at__isnull=True, voided_at__isnull=True).exclude(due__amount=0, due__vat_amount=0).exclude(due__status="cancelled").count()


@metric("club_credit_total", "Credit held for clubs (EUR)", group="attention")
def _club_credit_total(s):
    from billing.models import ClubCreditEntry

    return str(ClubCreditEntry.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0"))


@metric("renewals_pending", "Renewals waiting to be opened", group="attention")
def _renewals_pending(s):
    from billing.services.dues import subscriptions_due_for_renewal

    return len(subscriptions_due_for_renewal(s.today))


@metric("clubs_near_member_limit", f"Clubs at {int(AT_LIMIT_RATIO * 100)}%+ of their member limit", group="attention")
def _near_limit(s):
    return sum(1 for f in s.clubs if f.member_limit and (f.members or 0) >= f.member_limit * AT_LIMIT_RATIO)


@metric("admins_pending_mfa", "Platform staff and club admins without MFA", group="attention")
def _pending_mfa(s):
    from allauth.mfa.models import Authenticator

    from controlpanel.services.platform_admins import platform_admins

    people = {str(p.profile_id) for p in platform_admins()} | {uid for f in s.clubs for uid in (f.data.get("admin_ids") or [])}
    with_mfa = {str(uid) for uid in Authenticator.objects.values_list("user_id", flat=True)}
    return len(people - with_mfa)


@metric("failed_jobs_24h", "Background jobs that failed in the last 24 hours", group="attention")
def _failed_jobs(s):
    from django_celery_results.models import TaskResult

    since = timezone.now() - datetime.timedelta(hours=24)
    return TaskResult.objects.filter(status="FAILURE", date_done__gte=since).count()


@metric("club_creations_failed", "Club creation requests that failed", group="attention")
def _failed_creations(s):
    from controlpanel.models import ClubProvisioning

    return ClubProvisioning.objects.filter(status=ClubProvisioning.FAILED).count()


# --- sections built from the facts -----------------------------------------------------------------------------------


def _plan_distribution(s) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    for f in s.clubs:
        counts[f.plan or "default (free)"] += 1
    return [{"plan": plan, "clubs": n} for plan, n in sorted(counts.items(), key=lambda x: -x[1])]


def _feature_adoption(s) -> list[dict]:
    from features.catalog import CATALOG

    out = []
    for code, d in CATALOG.items():
        enabled = [f for f in s.clubs if f.entitlements.has(code)]
        overridden = [f for f in s.clubs if f.entitlements.entitlement(code).source == "override"]
        out.append({"code": code, "name": d.name, "clubs": len(enabled), "overridden": len(overridden)})
    return out


def _funnel(s) -> list[dict]:
    return [
        {"key": "clubs", "step": "Clubs", "count": len(s.clubs)},
        {"key": "members", "step": "With members", "count": sum(1 for f in s.clubs if (f.members or 0) > 1)},
        {"key": "billed", "step": "Billed", "count": sum(1 for f in s.clubs if f.billed)},
    ]


def _months(n: int = 12) -> list[datetime.date]:
    today = timezone.localdate().replace(day=1)
    out = []
    for _ in range(n):
        out.append(today)
        today = (today - datetime.timedelta(days=1)).replace(day=1)
    return out[::-1]


def _monthly(qs, date_field: str, value=None) -> list[dict]:
    value = value or Count("pk")
    months = _months()
    rows = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["v"] for r in qs.filter(**{f"{date_field}__gte": months[0]}).annotate(m=TruncMonth(date_field)).values("m").annotate(v=value)}
    return [{"month": m.isoformat()[:7], "value": str(rows.get(m, 0))} for m in months]


def _charts() -> dict:
    from billing.models import DuePayment
    from club.models import Club

    clubs = Club.objects.exclude(schema_name=get_public_schema_name())
    return {
        "new_clubs": _monthly(clubs, "created"),
        "payments_received_eur": _monthly(DuePayment.objects.exclude(method=DuePayment.CREDIT), "paid_at", Sum("amount")),  # credit is not money received
    }


def club_risk(f: ClubFacts, owed: Decimal, overdue: bool) -> tuple[str, str]:
    """Risk tier and the first reason that applies (first match wins)."""
    if overdue:
        return "high", "Dues overdue past grace"
    if f.member_limit and (f.members or 0) >= f.member_limit:
        return "watch", "At its member limit"
    if owed > 0:
        return "watch", "Dues outstanding"
    if not f.billed:
        return "watch", "Not being billed"
    return "ok", "Nothing needs attention"


def _clubs_by_risk(s) -> list[dict]:
    from billing.models import Due
    from billing.services.dues import dues_overdue

    overdue_clubs = set(dues_overdue(s.today).values_list("club_id", flat=True))
    owed: dict = defaultdict(Decimal)
    grace: dict = {}
    for d in Due.objects.filter(status__in=Due.OWING):
        owed[d.club_id] += d.balance
        grace[d.club_id] = min(grace.get(d.club_id, d.grace_until), d.grace_until)
    order = {"high": 0, "watch": 1, "ok": 2}
    rows = []
    for f in s.clubs:
        tier, reason = club_risk(f, owed[f.club.pk], f.club.pk in overdue_clubs)
        rows.append(
            {
                "club_id": str(f.club.pk),
                "name": f.club.name,
                "slug": f.club.slug,
                "plan": f.plan,
                "members": f.members,
                "member_limit": f.member_limit if f.entitlements.has("max_members") else None,
                "events_upcoming": (f.data.get("events") or {}).get("upcoming"),
                "dues_owed": str(owed[f.club.pk]),
                "grace_until": grace[f.club.pk].isoformat() if f.club.pk in grace else None,
                "risk": tier,
                "reason": reason,
            }
        )
    return sorted(rows, key=lambda r: (order[r["risk"]], r["name"].lower()))


def build_snapshot() -> dict:
    s = _gather()
    sections: dict[str, dict] = {"totals": {}, "attention": {}}
    for m in _metrics:
        sections[m.group][m.key] = {"label": m.label, "value": m.compute(s)}
    return {
        "generated_at": timezone.now().isoformat(),
        **sections,
        "funnel": _funnel(s),
        "plans": _plan_distribution(s),
        "features": _feature_adoption(s),
        "charts": _charts(),
        "clubs": _clubs_by_risk(s),
    }


def snapshot(*, refresh: bool = False) -> dict:
    if not refresh:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    data = build_snapshot()
    cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def invalidate() -> None:
    cache.delete(CACHE_KEY)


# --- one club ---------------------------------------------------------------------------------------------------------


def club_stats(club) -> dict:
    """Numbers for one club (runs queries inside its schema)."""
    from billing.models import Due
    from billing.services.dues import current_plan
    from features.entitlements import resolve_entitlements

    ent = resolve_entitlements(club)
    data = {}
    with schema_context(club.schema_name):
        for key, fn in _collectors.items():
            try:
                data[key] = fn()
            except Exception:
                data[key] = None
    from billing.models import ClubBilling

    owing = Due.objects.filter(club=club, status__in=Due.OWING)
    billing = ClubBilling.objects.filter(club=club).first()
    plan = current_plan(club)
    members = data.get("members") or 0
    return {
        "plan": plan.code if plan else None,
        "members": members,
        "member_limit": ent.limit("max_members") if ent.has("max_members") else None,
        "admins": len(data.get("admin_ids") or []),
        "dues_owed": str(sum((d.balance for d in owing), Decimal("0"))),
        "downgraded_at": billing.downgraded_at if billing else None,
        "trial_ends_at": billing.trial_ends_at if billing else None,
        "features": sorted(ent.enabled_codes()),
    }


# --- one club's overview page -------------------------------------------------------------------------------------------


def club_overview(club) -> dict:
    """What the club page charts and counts: signups, member types and the People/Members/Events/Shop boxes.

    Shop has no tables yet, so that box reports ``available: False`` instead of inventing zeros; it fills
    in when the app exists. ``renewal_rate`` is ``None`` for the same reason: there are no seasons or renewals to measure.
    """
    from authorization.catalog import OWNER_SLUG
    from authorization.models import Role
    from events.models import Event, Season
    from members.models import Member

    now = timezone.now()
    with schema_context(club.schema_name):
        members = Member.objects.all()
        total = members.count()
        new_30d = members.filter(created__gte=now - datetime.timedelta(days=30)).count()
        this_year = members.filter(created__year=now.year).count()
        with_login = members.filter(user__isnull=False).count()
        by_type = {
            Member.Role.MEMBER: members.members().count(),
            Member.Role.GUARDIAN: members.guardians().count(),
            Member.Role.STAFF: members.staff().count(),
        }
        signups = _monthly(members, "created")
        admins = len(Role.objects.get(slug=OWNER_SLUG).group.user_set.values_list("pk", flat=True))
        live = Event.objects.filter(cancelled=False)
        season = Season.get_current(timezone.localdate())
        in_season = live.filter(start__date__gte=season.start_date, start__date__lte=season.end_date) if season else live.none()
        events = {
            "upcoming": live.filter(start__gte=now).count(),
            "next_7d": live.filter(start__gte=now, start__lt=now + datetime.timedelta(days=7)).count(),
            "season": in_season.count(),
            "games_played": in_season.filter(kind=Event.EventKind.GAME, start__lt=now).count(),
            "has_season": season is not None,
        }

    # Roles overlap (a parent who plays counts as both), so these are per-role counts, not a partition.
    labels = dict(Member.Role.choices)
    types = [{"type": code, "label": labels[code], "count": n} for code, n in sorted(by_type.items(), key=lambda x: -x[1]) if n]
    return {
        "kpis": {"members": total, "new_members_30d": new_30d, "renewal_rate": None, "renewal_note": "Needs seasons and renewals, which don't exist yet."},
        "signups": signups,
        "member_types": types,
        "groups": [
            {"key": "people", "title": "People", "icon": "contact", "available": True, "stats": [{"label": "Admins", "value": admins}, {"label": "Members", "value": total}, {"label": "With a login", "value": with_login}]},
            {
                "key": "members",
                "title": "Members",
                "icon": "users",
                "available": True,
                "stats": [{"label": "Total", "value": total}, {"label": "Joined in the last 30 days", "value": new_30d}, {"label": "Joined this year", "value": this_year}, {"label": "Member types in use", "value": len(types)}],
            },
            {
                "key": "events",
                "title": "Events",
                "icon": "calendar-days",
                "available": True,
                "stats": [
                    {"label": "Upcoming", "value": events["upcoming"]},
                    {"label": "In the next 7 days", "value": events["next_7d"]},
                    {"label": "This season", "value": events["season"] if events["has_season"] else None},
                    {"label": "Games played this season", "value": events["games_played"] if events["has_season"] else None},
                ],
            },
            {
                "key": "shop",
                "title": "Shop",
                "icon": "shopping-cart",
                "available": False,
                "stats": [{"label": "Orders", "value": None}, {"label": "Revenue", "value": None}, {"label": "Outstanding", "value": None}, {"label": "Orders unpaid", "value": None}],
            },
        ],
        "home_location": location_data_for(club),
    }


def location_data_for(club) -> dict | None:
    from controlpanel.services.home_location import get_home_location

    data = get_home_location(club)
    return {**data, "id": str(data["id"])} if data else None
