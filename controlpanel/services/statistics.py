"""Platform and per-club statistics.

``club_statistics`` returns a list of stat *groups*, so growing the model later
means adding an entry here and nothing else. ``clubs_with_totals`` annotates in
a single query — the club list must not fan out into N+1.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from allauth.mfa.models import Authenticator
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db.models import Count, DateField, DecimalField, Exists, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from waffle import get_waffle_flag_model

from authentication.middleware import ELEVATED_ROLES
from billing.models import Due, DuePayment, Subscription
from billing.services.dues import dues_in_grace, dues_overdue, subscriptions_due_for_renewal
from club.models import Club, ClubMembership, ClubRole, Season
from events.models import Attendance, Event
from members.models import Member
from shop.models import Cart, Order
from teams.models import StaffAssignment, Team, TeamMembership

ZERO = Decimal("0.00")

PAID_STATUSES = (Order.OrderStatus.PAID, Order.OrderStatus.DELIVERED)
OWED_STATUSES = (Order.OrderStatus.PENDING, Order.OrderStatus.PARTIALLY_PAID)

#: A club with nothing scheduled inside this window has stopped using the product.
DORMANT_DAYS = 30
MONTHS_OF_HISTORY = 12


def clubs_with_totals(queryset=None):
    """Clubs annotated with headline counts (one query, no N+1)."""
    clubs = Club.objects.all() if queryset is None else queryset
    return clubs.annotate(
        member_count=Count("clubmemberships__member", distinct=True),
        team_count=Count("teams", distinct=True),
        event_count=Count("events", distinct=True),
        admin_count=Count("clubroles", filter=Q(clubroles__role=ClubRole.Roles.ADMIN), distinct=True),
    )


def _subquery(queryset, expression, output_field):
    """One aggregate, in its own subquery.

    Deliberately not a pile of annotate(Count(...), Sum(...)) on one queryset: aggregates
    that span *different* joins multiply each other's rows, so a club's outstanding total
    would come back doubled for every membership it happens to have. Subqueries each stand
    alone, so nothing can inflate anything else.
    """
    return Coalesce(Subquery(queryset.filter(club=OuterRef("pk")).values("club").annotate(value=expression).values("value"), output_field=output_field), Value(0), output_field=output_field)


def clubs_with_health(queryset=None, today=None, now=None):
    """Clubs annotated with the health of each — for the dashboard table, in one query."""
    today = today or timezone.localdate()
    now = now or timezone.now()
    clubs = Club.objects.active() if queryset is None else queryset

    in_season = Q(season__start_date__lte=today, season__end_date__gte=today)
    # A period the club is covered for, most recent first — paid or waived, both settled.
    _covered = Due.objects.filter(club=OuterRef("pk"), status__in=(Due.Status.PAID, Due.Status.WAIVED)).order_by("-period_end")
    managed_this_season = Q(
        staff_assignments__season__start_date__lte=today,
        staff_assignments__season__end_date__gte=today,
        staff_assignments__position__management_position=True,
    )

    return (
        clubs.annotate(
            has_season=Exists(Season.objects.filter(club=OuterRef("pk"), start_date__lte=today, end_date__gte=today)),
            active_members=_subquery(ClubMembership.objects.filter(in_season, status=ClubMembership.StatusChoices.ACTIVE), Count("pk"), IntegerField()),
            unpaid_members=_subquery(ClubMembership.objects.filter(in_season, fee_status=ClubMembership.FeeStatus.UNPAID), Count("pk"), IntegerField()),
            outstanding=_subquery(Order.objects.filter(status__in=OWED_STATUSES), Sum("total"), DecimalField(max_digits=10, decimal_places=2)),
            upcoming_events=_subquery(Event.objects.filter(start__gte=now, start__lte=now + timedelta(days=DORMANT_DAYS)), Count("pk"), IntegerField()),
            team_count=_subquery(Team.objects.all(), Count("pk"), IntegerField()),
            teams_managed=_subquery(Team.objects.filter(managed_this_season), Count("pk", distinct=True), IntegerField()),
            admin_count=_subquery(ClubRole.objects.filter(role=ClubRole.Roles.ADMIN), Count("pk"), IntegerField()),
            tier_name=Subquery(Subscription.objects.filter(club=OuterRef("pk")).values("tier__name")[:1]),
            dues_owed=_subquery(Due.objects.filter(status__in=Due.OWING), Sum(F("amount") - F("amount_paid")), DecimalField(max_digits=10, decimal_places=2)),
            dues_grace_until=Subquery(Due.objects.filter(club=OuterRef("pk"), status__in=Due.OWING).order_by("grace_until").values("grace_until")[:1]),
            dues_period_end=Subquery(Due.objects.filter(club=OuterRef("pk"), status__in=Due.OWING).order_by("period_end").values("period_end")[:1]),
            # How far the club is covered: the furthest-out period that is settled. PAID and
            # WAIVED both mean nothing is owed for that period, and its end is the day grace
            # would start if nothing renews — so both count. `covered_status` is read from the
            # same top row, so the table can badge "paid" vs "waived". Null when the club owes
            # or was never billed.
            covered_until=Subquery(_covered.values("period_end")[:1], output_field=DateField()),
            covered_status=Subquery(_covered.values("status")[:1]),
        )
        .annotate(teams_without_coach=F("team_count") - F("teams_managed"))
        .order_by("name")
    )


def platform_totals():
    return {
        "clubs": Club.objects.active().count(),
        "archived_clubs": Club.objects.archived().count(),
        "members": Member.objects.count(),
        "admins": ClubRole.objects.filter(role=ClubRole.Roles.ADMIN).count(),
    }


def clubs_without_a_season(today=None):
    """Clubs with no season covering today.

    Not cosmetic: seasons scope memberships, rosters and events, so a club without
    one cannot take a signup or schedule a match. It fails silently — nothing errors,
    the club is simply inert — which is exactly why it belongs on a dashboard.
    """
    today = today or timezone.localdate()
    return Club.objects.active().exclude(seasons__start_date__lte=today, seasons__end_date__gte=today)


def dormant_clubs(days=DORMANT_DAYS):
    """Active clubs with nothing on the calendar in the next ``days``. Churn signal."""
    now = timezone.now()
    return Club.objects.active().exclude(events__start__gte=now, events__start__lte=now + timedelta(days=days))


def admins_pending_mfa():
    """Privileged users who have not enrolled a second factor.

    They are locked out until they do (RequireMFAMiddleware redirects them to the
    enrolment page), so this is a support queue rather than a statistic. The rule is
    the middleware's own: platform staff, plus anyone holding an elevated ClubRole.
    """
    User = get_user_model()
    elevated = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True) | Q(member__roles__role__in=ELEVATED_ROLES))

    return elevated.exclude(pk__in=Authenticator.objects.values("user")).distinct()


def onboarding_funnel():
    """How far each active club got: created → has members → has a team → has events.

    Separates working clubs from empty shells someone created and walked away from,
    and shows which step people stall on.
    """
    clubs = clubs_with_totals(Club.objects.active())
    total = len(clubs)

    return [
        {"label": _("Clubs"), "count": total, "icon": "building-2"},
        {"label": _("With members"), "count": sum(1 for club in clubs if club.member_count), "icon": "users"},
        {"label": _("With a team"), "count": sum(1 for club in clubs if club.team_count), "icon": "trophy"},
        {"label": _("With events"), "count": sum(1 for club in clubs if club.event_count), "icon": "calendar-days"},
    ]


def flags_for_club(club):
    """Every flag, annotated with whether it is on for this club and why."""
    enabled_ids = set(club.flags.values_list("pk", flat=True))
    return [
        {
            "flag": flag,
            "enabled": flag.pk in enabled_ids,
            # `everyone` overrides club targeting, so the per-club toggle is moot.
            "overridden": flag.everyone is not None,
        }
        for flag in get_waffle_flag_model().objects.order_by("name")
    ]


def flag_adoption():
    """Clubs per feature flag. `everyone` overrides club targeting, so a flag set that
    way is on (or off) everywhere and its club count says nothing — hence `overridden`."""
    Flag = get_waffle_flag_model()

    return [{"name": flag.name, "clubs": flag.clubs.count(), "everyone": flag.everyone, "overridden": flag.everyone is not None} for flag in Flag.objects.annotate(club_total=Count("clubs")).order_by("name")]


def platform_attention():
    """The numbers that are supposed to be zero. A dashboard of healthy counts is a
    dashboard nobody opens."""
    members = Member.objects.count()

    return {
        "clubs_without_season": clubs_without_a_season().count(),
        "dormant_clubs": dormant_clubs().count(),
        "admins_pending_mfa": admins_pending_mfa().count(),
        "outstanding": _money(Order.objects.filter(status__in=OWED_STATUSES)),
        "members_without_login": Member.objects.filter(user__isnull=True).count(),
        "members": members,
        # Platform billing: what the clubs owe US. Distinct from `outstanding`, which is
        # what members owe their clubs — that money is never ours.
        "dues_owed": _dues_owed(),
        "dues_in_grace": dues_in_grace().count(),
        "dues_overdue": dues_overdue().count(),
        "clubs_unbilled": Club.objects.active().filter(subscription__isnull=True).count(),
        # Normally ~0: the renewal job keeps it there. A number that sits here means cron is
        # dead, and a club is about to use the platform for free — silently, because nothing is
        # owed, so no other number on this page would go red.
        "renewals_pending": len(subscriptions_due_for_renewal()),
    }


def _dues_owed():
    """What clubs owe the platform right now."""
    owed = Due.objects.filter(status__in=Due.OWING).aggregate(total=Sum(F("amount") - F("amount_paid")))["total"]

    return owed or ZERO


def _monthly(queryset, field, value, months=MONTHS_OF_HISTORY):
    """A dense month-by-month series — zero-filled, because a chart that silently skips
    empty months draws a smooth line over a month where nothing happened."""
    start = (timezone.now() - relativedelta(months=months)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    rows = queryset.filter(**{f"{field}__gte": start}).annotate(month=TruncMonth(field)).values("month").annotate(value=value).order_by("month")
    found = {row["month"].strftime("%Y-%m"): row["value"] or 0 for row in rows if row["month"]}

    series, cursor = [], start
    while cursor <= timezone.now():
        key = cursor.strftime("%Y-%m")
        series.append({"month": cursor.strftime("%b %Y"), "value": float(found.get(key, 0))})
        cursor = (cursor + timedelta(days=32)).replace(day=1)

    return series


def platform_charts():
    return {
        "signups": signup_split(),
        # Two different pots of money: `dues` is platform income (clubs paying us), while
        # `club_revenue` is members paying their clubs — never ours, and labelling it
        # "revenue" on our dashboard would be a lie.
        "dues": _monthly(DuePayment.objects.all(), "paid_at", Sum("amount")),
        "club_revenue": _monthly(Order.objects.filter(status__in=PAID_STATUSES), "created", Sum("total")),
    }


def _money(queryset):
    return queryset.aggregate(total=Sum("total"))["total"] or ZERO


def previous_season(club, season):
    """The season immediately before ``season``. Seasons are ordered by name (which is
    derived from the years), so go by the date instead — a club may skip a year."""
    if season is None:
        return None

    return Season.objects.filter(club=club, end_date__lt=season.start_date).order_by("-end_date").first()


def renewal_rate(club, season):
    """Share of last season's active members who signed up again.

    The single best health signal a club has, and it is exactly computable here because
    memberships are season-scoped. Returns None when there is no season to compare
    against — a first-season club has not failed to renew anyone, and rendering that as
    0% would libel it.
    """
    previous = previous_season(club, season)
    if previous is None:
        return None

    was_active = ClubMembership.objects.filter(club=club, season=previous, status=ClubMembership.StatusChoices.ACTIVE)
    total = was_active.count()
    if not total:
        return None

    returned = ClubMembership.objects.filter(club=club, season=season, member__in=was_active.values("member")).count()

    return round(100 * returned / total)


def new_members(club, season):
    """Members whose first-ever season at this club is ``season``.

    Keyed on "has no membership in an earlier season", not on "signed up recently" — a
    member who lapsed for a year and came back is a renewal, not a new member, and
    counting them as new would flatter every recovery into growth.
    """
    if season is None:
        return Member.objects.none()

    seen_before = ClubMembership.objects.filter(club=club, season__start_date__lt=season.start_date).values("member")

    return Member.objects.filter(member_of__club=club, member_of__season=season).exclude(pk__in=seen_before).distinct()


def signup_split(club=None, months=MONTHS_OF_HISTORY):
    """Signups per month, split into first-timers and returners. ``club=None`` is platform-wide.

    "First" is keyed on (club, member), never on the member alone — the same person can be
    new at one club while renewing at another, and collapsing that would mark their second
    club's very first signup as a renewal.

    Each member's earliest season is resolved once up front rather than per row: the same
    question asked inside a loop is one query per membership.
    """
    start = (timezone.now() - relativedelta(months=months)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    memberships = ClubMembership.objects.all() if club is None else ClubMembership.objects.filter(club=club)

    first_season = {}
    for club_id, member_id, season_start in memberships.values_list("club_id", "member_id", "season__start_date"):
        key = (club_id, member_id)
        if key not in first_season or season_start < first_season[key]:
            first_season[key] = season_start

    counts = defaultdict(lambda: {"new": 0, "returning": 0})
    for club_id, member_id, season_start, signed_up_at in memberships.filter(signed_up_at__isnull=False, signed_up_at__gte=start).values_list("club_id", "member_id", "season__start_date", "signed_up_at"):
        kind = "new" if season_start == first_season[(club_id, member_id)] else "returning"
        counts[signed_up_at.strftime("%Y-%m")][kind] += 1

    series, cursor = [], start
    while cursor <= timezone.now():
        month = counts[cursor.strftime("%Y-%m")]
        series.append({"month": cursor.strftime("%b %Y"), "new": month["new"], "returning": month["returning"]})
        cursor = (cursor + timedelta(days=32)).replace(day=1)

    return series


def teams_without_a_manager(club, season):
    """Teams with nobody in a management position this season.

    A defect in the club's own setup, not a statistic: without a coach or manager the
    access service grants nobody authority over that team, so nobody can pick the squad.
    """
    if season is None:
        return Team.objects.none()

    return Team.objects.filter(club=club).exclude(staff_assignments__season=season, staff_assignments__position__management_position=True)


def unrostered_members(club, season):
    """Active members who are on no team this season — people who paid and play nowhere."""
    if season is None:
        return Member.objects.none()

    rostered = TeamMembership.objects.filter(team__club=club, season=season).values("member")

    return Member.objects.filter(member_of__club=club, member_of__season=season, member_of__status=ClubMembership.StatusChoices.ACTIVE).exclude(pk__in=rostered).distinct()


def fee_aging(club):
    """Unpaid orders bucketed by age. "€2,400 overdue past 60 days" drives a phone call;
    "€2,400 outstanding" does not."""
    now = timezone.now()
    owed = Order.objects.filter(club=club, status__in=OWED_STATUSES)

    buckets = []
    for label, older_than, newer_than in ((_("0-30 days"), 0, 30), (_("30-60 days"), 30, 60), (_("60+ days"), 60, None)):
        rows = owed.filter(created__lte=now - timedelta(days=older_than))
        if newer_than is not None:
            rows = rows.filter(created__gt=now - timedelta(days=newer_than))
        buckets.append({"label": label, "total": _money(rows), "count": rows.count(), "overdue": newer_than is None})

    return buckets


def attendance_rates(club, season):
    """Turnout, and how many never answered.

    The no-response share is the leading indicator: it measures whether members are using
    the app at all, which every other number here depends on.
    """
    if season is None:
        return {"turnout": None, "no_response": None, "responses": 0}

    counts = Attendance.objects.filter(event__club=club, event__season=season, event__start__lt=timezone.now()).aggregate(
        present=Count("id", filter=Q(status=Attendance.AttendanceStatus.PRESENT)),
        absent=Count("id", filter=Q(status=Attendance.AttendanceStatus.ABSENT)),
        silent=Count("id", filter=Q(status=Attendance.AttendanceStatus.NO_RESPONSE)),
        total=Count("id"),
    )

    answered = counts["present"] + counts["absent"]

    return {
        "turnout": round(100 * counts["present"] / answered) if answered else None,
        "no_response": round(100 * counts["silent"] / counts["total"]) if counts["total"] else None,
        "responses": counts["total"],
    }


def club_attention(club):
    """A club's own numbers that are supposed to be zero."""
    season = Season.covering(club, timezone.localdate())
    memberships = ClubMembership.objects.filter(club=club)

    return {
        "season": season,
        "no_season": season is None,
        "outstanding": _money(Order.objects.filter(club=club, status__in=OWED_STATUSES)),
        "aging": fee_aging(club),
        "unpaid_members": memberships.filter(season=season, fee_status=ClubMembership.FeeStatus.UNPAID).count() if season else 0,
        "pending_approvals": memberships.filter(status=ClubMembership.StatusChoices.PENDING).count(),
        "teams_without_manager": teams_without_a_manager(club, season).count(),
        "unrostered": unrostered_members(club, season).count(),
        "new_members": new_members(club, season).count(),
        "renewal_rate": renewal_rate(club, season),
        "attendance": attendance_rates(club, season),
    }


def club_charts(club):
    season = Season.covering(club, timezone.localdate())
    memberships = ClubMembership.objects.filter(club=club, season=season) if season else ClubMembership.objects.none()

    return {
        "signups": signup_split(club),
        # Fee status this season, in the order a treasurer cares about.
        "fees": [
            {"label": label, "value": memberships.filter(fee_status=status).count()}
            for status, label in (
                (ClubMembership.FeeStatus.PAID, _("Paid")),
                (ClubMembership.FeeStatus.PARTIALLY_PAID, _("Partial")),
                (ClubMembership.FeeStatus.UNPAID, _("Unpaid")),
                (ClubMembership.FeeStatus.WAIVED, _("Waived")),
            )
        ],
    }


def club_statistics(club):
    """Stat groups for one club. Add new groups here as the domain grows."""
    season = Season.covering(club, timezone.localdate())
    now = timezone.now()

    memberships = ClubMembership.objects.filter(club=club)
    events = Event.objects.filter(club=club)
    orders = Order.objects.filter(club=club)

    return [
        {
            "title": _("Members"),
            "icon": "users",
            "stats": [
                (_("Members"), memberships.values("member").distinct().count()),
                (_("Active this season"), memberships.filter(season=season, status=ClubMembership.StatusChoices.ACTIVE).count() if season else 0),
                (_("Pending"), memberships.filter(status=ClubMembership.StatusChoices.PENDING).count()),
                (_("Lapsed"), memberships.filter(status=ClubMembership.StatusChoices.LAPSED).count()),
            ],
        },
        {
            "title": _("Teams & staff"),
            "icon": "shield",
            "stats": [
                (_("Teams"), Team.objects.filter(club=club).count()),
                (_("Players this season"), TeamMembership.objects.filter(team__club=club, season=season).count() if season else 0),
                (_("Staff this season"), StaffAssignment.objects.filter(team__club=club, season=season).count() if season else 0),
            ],
        },
        {
            "title": _("Events"),
            "icon": "calendar-days",
            "stats": [
                (_("Upcoming"), events.filter(start__gte=now).count()),
                (_("This season"), events.filter(season=season).count() if season else 0),
            ],
        },
        {
            "title": _("Shop"),
            "icon": "shopping-cart",
            "stats": [
                (_("Orders"), orders.count()),
                (_("Revenue"), _money(orders.filter(status__in=PAID_STATUSES))),
                (_("Outstanding"), _money(orders.filter(status__in=OWED_STATUSES))),
                (_("Open carts"), Cart.objects.filter(club=club, status=Cart.CartStatus.OPEN).count()),
            ],
        },
    ]
