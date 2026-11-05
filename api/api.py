from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django_tenants.utils import get_public_schema_name
from ninja import NinjaAPI

from authorization.permissions import is_club_admin
from authorization.services import user_perm_codes
from club.icons import ensure_icons
from club.models import Domain
from members.models import Member
from members.services import access

from .auth import AUTHENTICATORS
from .errors import register_error_handlers
from .routers import calendar_data as calendar_data_router
from .routers import club_settings as club_settings_router
from .routers import events as events_router
from .routers import members as members_router
from .routers import memberships as memberships_router
from .routers import notifications_inbox as notifications_router
from .routers import officiating_levels as officiating_levels_router
from .routers import overview as overview_router
from .routers import teams as teams_router
from .routers import team_settings as team_settings_router
from .schemas import BrandingOut, MeOut, MyClubOut

api = NinjaAPI(
    title="RosterChief API",
    version="1",
    description="Authentication (login, MFA, passkeys) is served by allauth headless under /_allauth/.",
    auth=AUTHENTICATORS,
    urls_namespace="api",
    docs_url="/docs" if settings.DEBUG else None,
)
register_error_handlers(api)
api.add_router("/members", members_router.router, tags=["members"])
api.add_router("/memberships", memberships_router.router, tags=["memberships"])
api.add_router("/club", club_settings_router.router, tags=["club settings"])
api.add_router("/officiating", officiating_levels_router.router, tags=["officiating levels"])
api.add_router("/notifications", notifications_router.router, tags=["notifications"])
api.add_router("/overview", overview_router.router, tags=["overview"])
api.add_router("/locations", calendar_data_router.locations_router, tags=["calendar"])
api.add_router("/opponents", calendar_data_router.opponents_router, tags=["calendar"])
api.add_router("/teams", teams_router.router, tags=["teams"])
api.add_router("/events", events_router.router, tags=["events"])
api.add_router("", team_settings_router.router, tags=["team settings"])


def _member_id(user, club) -> str | None:
    """The user's own member record in this club (none on the platform host or for a login without one)."""
    if club.schema_name == get_public_schema_name():
        return None
    member = Member.objects.filter(user=user).first()
    return str(member.pk) if member else None


@api.get("/me", response=MeOut, summary="The signed-in user in the current club")
def me(request):
    """Who am I here: identity, roles in this club and the features the club has."""
    user, club = request.user, request.tenant
    try:
        roles = sorted(user.tenant_perms.groups.values_list("name", flat=True))
    except ObjectDoesNotExist:
        raise PermissionDenied from None  # signed in, but not a user of this club
    return {
        "id": str(user.pk),
        "member_id": _member_id(user, club),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "club": {"slug": club.slug, "name": club.name, "is_platform": club.schema_name == get_public_schema_name()},
        "roles": roles,
        "permissions": sorted(user_perm_codes(user)),
        "is_club_admin": is_club_admin(user),
        "can_manage_members": access.can_manage_members(user),
        "can_manage_settings": access.can_manage_settings(user),
        "can_execute_evaluations": access.can_execute_evaluations(user),
        "managed_team_ids": [] if club.schema_name == get_public_schema_name() else sorted(str(i) for i in access.managed_team_ids(user)),
        "features": sorted(request.features.enabled_codes()),
    }


def _logo_url(club) -> str | None:
    return club.logo.url if club.logo else None


@api.get("/club", response=BrandingOut, auth=None, summary="Public branding of the club behind this host")
def club_branding(request):
    """Unauthenticated, used by the club-branded login page. On the bare domain it describes the platform itself.
    Archived or unknown clubs never reach here: their host answers 404 before the API."""
    club = request.tenant
    if club.schema_name == get_public_schema_name():
        return {"slug": "", "name": "RosterChief", "is_platform": True}
    return {
        "slug": club.slug,
        "name": club.name,
        "is_platform": False,
        "logo_url": _logo_url(club),
        "icons": ensure_icons(club) if club.logo else {},
        "primary_color": club.primary_color,
        "secondary_color": club.secondary_color,
        "primary_content_color": club.primary_content_color,
        "secondary_content_color": club.secondary_content_color,
    }


@api.get("/my-clubs", response=list[MyClubOut], summary="The clubs the signed-in user belongs to")
def my_clubs(request):
    """For the club picker on the bare domain. Archived clubs and the platform tenant are left out.
    ``host`` is the club's primary domain; the caller builds the URL with its own scheme and port."""
    public = get_public_schema_name()
    clubs = request.user.tenants.filter(archived_at__isnull=True).exclude(schema_name=public).order_by("name")
    primary = {d.tenant_id: d.domain for d in Domain.objects.filter(tenant__in=clubs, is_primary=True)}
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "host": primary[c.pk],
            "logo_url": _logo_url(c),
            "primary_color": c.primary_color,
            "primary_content_color": c.primary_content_color,
        }
        for c in clubs
        if c.pk in primary
    ]
