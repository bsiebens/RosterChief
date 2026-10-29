"""Management API: the caller's capability document (API.md §17.3, cross-cutting item 3).

``GET /manage/me`` tells a standalone frontend who the caller is in this club and which
management areas it may open or change, so navigation needs no guessing. Every flag comes from
the ``club.services.access`` predicate the matching management view (``management.views`` and its
``club.mixins`` gate) uses; this module only maps views to areas. Control-panel areas never appear.
"""

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.panel.guards import panel_club
from api.panel.schemas.me import MeOut
from api.panel.schemas.teams import SeasonOut
from api.protected.schemas import ErrorOut
from api.protected.services import club_features
from club.services.access import (
    can_add_news,
    can_manage_evaluations,
    can_manage_forms,
    can_manage_members,
    can_manage_referees,
    can_manage_shop,
    current_season,
    groups_manageable_by,
    has_management_access,
    is_club_admin,
    is_coach_manager,
    is_platform_superuser,
    roles_in_club,
    teams_managed_by,
)

router = Router(tags=["panel: me"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _manages_events(user, club) -> bool:
    """EventCreateView is any staff, but the form only lets an admin, a team manager or a group manager create anything."""
    return is_club_admin(user, club) or teams_managed_by(user, club).exists() or groups_manageable_by(user, club).exists()


def _management_position(user, club) -> bool:
    """ManagementPositionRequiredMixin (locations, opponents)."""
    return is_club_admin(user, club) or is_coach_manager(user, club)


# (key, label, feature, view predicate, manage predicate), in navigation order. ``feature`` is a club_features key.
AREAS = (
    ("members", _("Members"), None, has_management_access, can_manage_members),
    ("signup", _("Sign-ups"), None, can_manage_members, can_manage_members),
    ("memberships", _("Membership fees"), None, is_club_admin, is_club_admin),
    ("families", _("Families"), None, has_management_access, can_manage_members),
    ("groups", _("Groups"), None, can_manage_members, can_manage_members),
    ("volunteers", _("Volunteers"), None, can_manage_members, can_manage_members),
    ("teams", _("Teams"), None, has_management_access, can_manage_members),
    ("seasons", _("Seasons"), None, has_management_access, is_club_admin),
    ("positions", _("Positions"), None, has_management_access, is_club_admin),
    ("number_pools", _("Number pools"), None, has_management_access, is_club_admin),
    ("numbers", _("Jersey numbers"), None, has_management_access, has_management_access),
    ("events", _("Events"), None, has_management_access, _manages_events),
    ("locations", _("Locations"), None, _management_position, _management_position),
    ("opponents", _("Opponents"), None, _management_position, _management_position),
    ("referees", _("Referees"), None, has_management_access, can_manage_referees),
    ("referee_levels", _("Referee levels"), None, has_management_access, can_manage_members),
    ("officials", _("Officials"), "officials", has_management_access, can_manage_referees),
    ("official_levels", _("Official levels"), "officials", has_management_access, can_manage_members),
    ("news", _("News"), None, has_management_access, can_add_news),
    ("forms", _("Forms"), "forms", can_manage_forms, can_manage_forms),
    ("registrations", _("Registrations"), None, is_club_admin, is_club_admin),
    ("evaluations", _("Evaluations"), "evaluations", can_manage_evaluations, can_manage_evaluations),
    ("shop", _("Shop"), "shop", can_manage_shop, can_manage_shop),
    ("invoices", _("Invoices"), None, has_management_access, is_club_admin),
    ("sponsors", _("Sponsors"), None, is_club_admin, is_club_admin),
    ("roles", _("Roles"), None, is_club_admin, is_club_admin),
    ("onboarding_requirements", _("Onboarding requirements"), None, can_manage_members, can_manage_members),
    ("settings", _("Club settings"), None, is_club_admin, is_club_admin),
    ("bugs", _("Bug reports"), None, has_management_access, has_management_access),
)


def _areas(user, club, features: dict) -> list[dict]:
    areas = []
    for key, label, feature, view_predicate, manage_predicate in AREAS:
        available = feature is None or bool(features.get(feature))
        can_view = available and bool(view_predicate(user, club))
        can_manage = can_view and bool(manage_predicate(user, club))
        areas.append({"key": key, "label": str(label), "feature": feature, "available": available, "can_view": can_view, "can_manage": can_manage})
    return areas


@router.get("/manage/me", response={200: MeOut, **ERRORS}, summary="Who the caller is in this club and which management areas they may open")
def me(request):
    club = panel_club(request)
    user = request.user
    member = getattr(user, "member", None)
    season = current_season(club)
    features = club_features(club)
    return {
        "member": {"id": member.pk if member else None, "name": str(member) if member else user.get_full_name() or user.email},
        "club": {"id": club.pk, "name": club.name, "slug": club.slug},
        "season": SeasonOut(id=season.pk, name=season.name, start_date=season.start_date, end_date=season.end_date, is_current=True) if season else None,
        "roles": sorted(roles_in_club(user, club)),
        "is_platform_superuser": is_platform_superuser(user),
        "club_features": features,
        "areas": _areas(user, club, features),
    }
