"""Platform and per-club statistics.

``club_statistics`` returns a list of stat *groups*, so growing the model later
means adding an entry here and nothing else. ``clubs_with_totals`` annotates in
a single query — the club list must not fan out into N+1.
"""

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from club.models import Club, ClubMembership, ClubRole, Season
from events.models import Event
from members.models import Member
from shop.models import Cart, Order
from teams.models import StaffAssignment, Team, TeamMembership

ZERO = Decimal("0.00")

PAID_STATUSES = (Order.OrderStatus.PAID, Order.OrderStatus.DELIVERED)
OWED_STATUSES = (Order.OrderStatus.PENDING, Order.OrderStatus.PARTIALLY_PAID)


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
            "stats": [
                ("Members", memberships.values("member").distinct().count()),
                ("Active this season", memberships.filter(season=season, status=ClubMembership.StatusChoices.ACTIVE).count() if season else 0),
                ("Pending", memberships.filter(status=ClubMembership.StatusChoices.PENDING).count()),
                ("Lapsed", memberships.filter(status=ClubMembership.StatusChoices.LAPSED).count()),
            ],
        },
        {
            "title": "Teams & staff",
            "stats": [
                ("Teams", Team.objects.filter(club=club).count()),
                ("Players this season", TeamMembership.objects.filter(team__club=club, season=season).count() if season else 0),
                ("Staff this season", StaffAssignment.objects.filter(team__club=club, season=season).count() if season else 0),
            ],
        },
        {
            "title": "Events",
            "stats": [
                ("Upcoming", events.filter(start__gte=now).count()),
                ("This season", events.filter(season=season).count() if season else 0),
            ],
        },
        {
            "title": "Shop",
            "stats": [
                ("Orders", orders.count()),
                ("Revenue", _money(orders.filter(status__in=PAID_STATUSES))),
                ("Outstanding", _money(orders.filter(status__in=OWED_STATUSES))),
                ("Open carts", Cart.objects.filter(club=club, status=Cart.CartStatus.OPEN).count()),
            ],
        },
    ]
