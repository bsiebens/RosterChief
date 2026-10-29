"""Management overview (``GET /manage/home``): the JSON of the /manage home screen.

Any member of club staff may read it (``ClubStaffRequiredMixin``). The context comes from
``club.services.dashboard.home_context``, the service HomeView calls; it also decides who gets the
financial parts (charts, outstanding money, billing period, registrations queue, Shop figures: club
admins only), which this router leaves ``null`` / out for everyone else.
"""

from ninja import Router

from api.core.schemas import ErrorOut
from api.panel.guards import panel_club
from api.panel.schemas import dashboard as s
from club.services.dashboard import home_context
from members.services.lookup import get_request_member
from notifications.services import unread_count

router = Router(tags=["panel: dashboard"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _finance(context: dict) -> dict | None:
    if not context["is_club_admin"]:
        return None
    attention, charts = context["attention"], context["charts"]
    return {
        "signups": charts["signups"],
        "fees": [{"label": str(row["label"]), "value": row["value"]} for row in charts["fees"]],
        "renewal_rate": attention["renewal_rate"],
        "outstanding": attention["outstanding"],
        "unpaid_members": attention.get("unpaid_members", 0),
        "aging": [{**row, "label": str(row["label"])} for row in attention["aging"]],
    }


@router.get("/manage/home", response={200: s.HomeOut, **ERRORS}, summary="Management overview")
def home(request):
    """KPIs, what needs attention, upcoming events, latest news and statistic groups of the club.
    `finance`, `billing`, the registrations queue and the `Shop` group are club-admin only."""
    club = panel_club(request)
    context = home_context(club, request.user)
    attention, season = context["attention"], context["attention"]["season"]
    member = get_request_member(request)
    admin = context["is_club_admin"]
    configured = context["requirements_configured"]
    return {
        "season": {"id": season.pk, "name": str(season), "start_date": season.start_date, "end_date": season.end_date} if season else None,
        "kpis": {
            "member_count": context["member_count"],
            "member_count_change": context["member_count_change"],
            "pending_approvals": attention["pending_approvals"],
            "teams_without_manager": attention["teams_without_manager"],
            "unrostered": attention["unrostered"],
            "new_members": attention["new_members"],
            "attendance_turnout": attention["attendance"]["turnout"],
            "missing_documentation_count": context["missing_documentation_count"] if configured else None,
        },
        "attention": {
            "pending_approvals": attention["pending_approvals"],
            "teams_without_manager": attention["teams_without_manager"],
            "unrostered": attention["unrostered"],
            "missing_documentation_count": context["missing_documentation_count"],
            "registrations_awaiting_count": context["registrations_awaiting_count"],
        },
        "upcoming_events": [{"id": event.pk, "title": event.title, "start": event.start, "teams": [{"id": team.pk, "name": team.name, "short_name": team.short_name} for team in event.teams.all()]} for event in context["upcoming_events"]],
        "news": [{"id": item.pk, "title": item.title, "published_at": item.published_at} for item in context["published_news"]],
        "groups": [{"title": str(group["title"]), "icon": group["icon"], "stats": [{"label": str(label), "value": value} for label, value in group["stats"]]} for group in context["groups"]],
        "finance": _finance(context),
        "billing": {"ends_at": context["billing_ends_at"], "auto_renews": context["billing_auto_renews"]} if admin and context["billing_ends_at"] else None,
        "unread_notifications": unread_count(club, [member]) if member is not None else None,
    }
