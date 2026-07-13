"""Platform and per-club statistics.

``club_statistics`` returns a list of stat *groups*, so growing the model later
means adding an entry here and nothing else. ``clubs_with_totals`` annotates in
a single query — the club list must not fan out into N+1.
"""

from datetime import timedelta
from decimal import Decimal

from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from waffle import get_waffle_flag_model

from authentication.middleware import ELEVATED_ROLES
from club.models import Club, ClubMembership, ClubRole, Season
from events.models import Event
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
        {"label": "Clubs", "count": total, "icon": "building-2"},
        {"label": "With members", "count": sum(1 for club in clubs if club.member_count), "icon": "users"},
        {"label": "With a team", "count": sum(1 for club in clubs if club.team_count), "icon": "shield"},
        {"label": "With events", "count": sum(1 for club in clubs if club.event_count), "icon": "calendar-days"},
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
    }


def _monthly(queryset, field, value, months=MONTHS_OF_HISTORY):
    """A dense month-by-month series — zero-filled, because a chart that silently skips
    empty months draws a smooth line over a month where nothing happened."""
    start = (timezone.now() - timedelta(days=30 * months)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

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
        "signups": _monthly(ClubMembership.objects.filter(signed_up_at__isnull=False), "signed_up_at", Count("id")),
        "revenue": _monthly(Order.objects.filter(status__in=PAID_STATUSES), "created", Sum("total")),
    }


def _money(queryset):
    return queryset.aggregate(total=Sum("total"))["total"] or ZERO


def club_statistics(club):
    """Stat groups for one club. Add new groups here as the domain grows."""
    season = Season.covering(club, timezone.localdate())
    now = timezone.now()

    memberships = ClubMembership.objects.filter(club=club)
    events = Event.objects.filter(club=club)
    orders = Order.objects.filter(club=club)

    return [
        {
            "title": "Members",
            "icon": "users",
            "stats": [
                ("Members", memberships.values("member").distinct().count()),
                ("Active this season", memberships.filter(season=season, status=ClubMembership.StatusChoices.ACTIVE).count() if season else 0),
                ("Pending", memberships.filter(status=ClubMembership.StatusChoices.PENDING).count()),
                ("Lapsed", memberships.filter(status=ClubMembership.StatusChoices.LAPSED).count()),
            ],
        },
        {
            "title": "Teams & staff",
            "icon": "shield",
            "stats": [
                ("Teams", Team.objects.filter(club=club).count()),
                ("Players this season", TeamMembership.objects.filter(team__club=club, season=season).count() if season else 0),
                ("Staff this season", StaffAssignment.objects.filter(team__club=club, season=season).count() if season else 0),
            ],
        },
        {
            "title": "Events",
            "icon": "calendar-days",
            "stats": [
                ("Upcoming", events.filter(start__gte=now).count()),
                ("This season", events.filter(season=season).count() if season else 0),
            ],
        },
        {
            "title": "Shop",
            "icon": "shopping-cart",
            "stats": [
                ("Orders", orders.count()),
                ("Revenue", _money(orders.filter(status__in=PAID_STATUSES))),
                ("Outstanding", _money(orders.filter(status__in=OWED_STATUSES))),
                ("Open carts", Cart.objects.filter(club=club, status=Cart.CartStatus.OPEN).count()),
            ],
        },
    ]
