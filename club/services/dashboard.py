"""What the management overview ("Home") shows, shared by the /manage HomeView and ``GET /manage/home``.

``home_context`` returns raw values (model instances, Decimals, lazy labels) for the caller to render
or serialise. Who sees what is decided here, not in the template: the charts, the billing-period notice,
the registrations queue and the Shop figures are for a club admin only; everything else is for any
member of staff. Upcoming events are narrowed to the caller's managed teams like the events list.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from billing.models import Due
from club.models import ClubMembership, OnboardingRequirement, Season
from club.services.access import is_club_admin, scoped_to_managed_teams
from club.services.onboarding import annotate_onboarding_status
from controlpanel.services.statistics import club_attention, club_charts, club_statistics
from events.models import Event
from news.models import News

UPCOMING_LIMIT = 8
NEWS_LIMIT = 5


def billing_period_ending(club):
    """``(period_end, auto_renews)`` when the club's billing period ends within the plan's renewal lead,
    else ``(None, False)``. Only worth raising when nothing is owed: an unpaid club gets the (louder)
    billing notice instead."""
    subscription = getattr(club, "subscription", None)
    if subscription is None:
        return None, False
    if club.dues.filter(status__in=Due.OWING).exists():
        return None, subscription.auto_renew
    latest_due = club.dues.exclude(status=Due.Status.CANCELLED).order_by("-period_end").first()
    if latest_due is not None and 0 <= (latest_due.period_end - timezone.localdate()).days <= subscription.plan.renewal_lead_days:
        return latest_due.period_end, subscription.auto_renew
    return None, subscription.auto_renew


def home_context(club, user) -> dict:
    admin = is_club_admin(user, club)
    billing_ends_at, billing_auto_renews = billing_period_ending(club)

    upcoming_events = scoped_to_managed_teams(Event.objects.filter(club=club, start__gte=timezone.now()), user, club).order_by("start").prefetch_related("teams")[:UPCOMING_LIMIT]

    attention = club_attention(club)
    season = attention["season"]

    # Compared against the season right before this one (Season.before), not last year on the calendar.
    member_count = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).count() if season else 0
    previous_season = Season.before(club, season) if season else None
    member_count_change = None
    if previous_season is not None:
        member_count_change = member_count - ClubMembership.objects.filter(club=club, season=previous_season, kind=ClubMembership.Kind.MEMBER).count()

    # Hidden entirely (not zeroed) for a club tracking no onboarding requirements: "0 missing" would
    # read as "everyone's paperwork is in" when nothing is tracked at all.
    requirements_configured = OnboardingRequirement.objects.filter(club=club, is_active=True).exists()
    missing_documentation_count = 0
    if requirements_configured and season is not None:
        memberships_this_season = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER)
        missing_documentation_count = sum(1 for membership in annotate_onboarding_status(memberships_this_season) if membership.onboarding_open)

    registrations_awaiting_count = None
    if admin:
        from registration.services.invoicing import registrations_awaiting_confirmation

        registrations_awaiting_count = len(registrations_awaiting_confirmation(club))

    shop_title = str(_("Shop"))
    groups = [group for group in club_statistics(club) if admin or str(group["title"]) != shop_title]

    return {
        "is_club_admin": admin,
        "attention": attention,
        "charts": club_charts(club) if admin else None,
        "groups": groups,
        "upcoming_events": upcoming_events,
        "member_count": member_count,
        "member_count_change": member_count_change,
        "requirements_configured": requirements_configured,
        "missing_documentation_count": missing_documentation_count,
        "registrations_awaiting_count": registrations_awaiting_count,
        "published_news": News.objects.filter(club=club, status=News.Status.PUBLISHED, published_at__lte=timezone.now()).order_by("-published_at")[:NEWS_LIMIT],
        "billing_ends_at": billing_ends_at,
        "billing_auto_renews": billing_auto_renews,
        "today": timezone.localdate(),
    }
