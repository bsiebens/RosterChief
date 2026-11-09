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
from .routers import analytics as analytics_router
from .routers import calendar_data as calendar_data_router
from .routers import club_settings as club_settings_router
from .routers import evaluations as evaluations_router
from .routers import event_subtypes as event_subtypes_router
from .routers import events as events_router
from .routers import fixture_imports as fixture_imports_router
from .routers import forms as forms_router
from .routers import member_stats as member_stats_router
from .routers import members as members_router
from .routers import memberships as memberships_router
from .routers import news as news_router
from .routers import notifications_inbox as notifications_router
from .routers import officiating_levels as officiating_levels_router
from .routers import overview as overview_router
from .routers import public_forms as public_forms_router
from .routers import public_news as public_news_router
from .routers import public_stats as public_stats_router
from .routers import referees as referees_router
from .routers import requirements as requirements_router
from .routers import roles as roles_router
from .routers import shop_catalog as shop_catalog_router
from .routers import signups as signups_router
from .routers import shop_delivery as shop_delivery_router
from .routers import shop_discounts as shop_discounts_router
from .routers import shop_inventory as shop_inventory_router
from .routers import shop_invoices as shop_invoices_router
from .routers import shop_orders as shop_orders_router
from .routers import shop_payments as shop_payments_router
from .routers import shop_pickup as shop_pickup_router
from .routers import shop_production as shop_production_router
from .routers import team_settings as team_settings_router
from .routers import teams as teams_router
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
api.add_router("/referee-management", referees_router.router, tags=["referee management"])
api.add_router("/requirements", requirements_router.router, tags=["requirements"])
api.add_router("/news", news_router.router, tags=["news"])
api.add_router("/roles", roles_router.router, tags=["roles"])
api.add_router("/signups", signups_router.router, tags=["signups"])
api.add_router("/evaluations", evaluations_router.router, tags=["evaluations"])
api.add_router("/forms", forms_router.router, tags=["forms"])
api.add_router("/form-sends", forms_router.sends_router, tags=["forms"])
api.add_router("/form-submissions", forms_router.submissions_router, tags=["forms"])
api.add_router("/form-answers", forms_router.answers_router, tags=["forms"])
api.add_router("/shop", shop_catalog_router.router, tags=["shop"])
api.add_router("/shop/inventory", shop_inventory_router.router, tags=["shop"])
api.add_router("/shop/discounts", shop_discounts_router.router, tags=["shop"])
api.add_router("/shop/vouchers", shop_discounts_router.vouchers_router, tags=["shop"])
api.add_router("/shop/orders", shop_orders_router.router, tags=["shop"])
api.add_router("/shop/orders", shop_payments_router.router, tags=["shop"])
api.add_router("/shop", shop_payments_router.settings_router, tags=["shop"])
api.add_router("/shop", shop_orders_router.stats_router, tags=["shop"])
api.add_router("/shop", shop_invoices_router.router, tags=["shop"])
api.add_router("/shop/production", shop_production_router.router, tags=["shop"])
api.add_router("/shop/pickup", shop_pickup_router.router, tags=["shop"])
api.add_router("/shop/delivery", shop_delivery_router.router, tags=["shop"])
api.add_router("/public/shop", shop_payments_router.public_router, tags=["public"])
api.add_router("/notifications", notifications_router.router, tags=["notifications"])
api.add_router("/overview", overview_router.router, tags=["overview"])
api.add_router("/locations", calendar_data_router.locations_router, tags=["calendar"])
api.add_router("/opponents", calendar_data_router.opponents_router, tags=["calendar"])
api.add_router("/teams", teams_router.router, tags=["teams"])
api.add_router("/teams", fixture_imports_router.teams_router, tags=["fixture imports"])
api.add_router("/fixture-imports", fixture_imports_router.json_router, tags=["fixture imports"])
api.add_router("/events", events_router.router, tags=["events"])
api.add_router("/teams", analytics_router.teams_router, tags=["analytics"])
api.add_router("/members", analytics_router.members_router, tags=["analytics"])
api.add_router("/members", member_stats_router.router, tags=["members"])
api.add_router("/public", public_stats_router.router, tags=["public"])
api.add_router("/public/news", public_news_router.router, tags=["public"])
api.add_router("/public/forms", public_forms_router.router, tags=["public"])
api.add_router("/events", analytics_router.events_router, tags=["analytics"])
api.add_router("", team_settings_router.router, tags=["team settings"])
api.add_router("", event_subtypes_router.router, tags=["event subtypes"])


def _member_id(user, club) -> str | None:
    """The user's own member record in this club (none on the platform host or for a login without one)."""
    if club.schema_name == get_public_schema_name():
        return None
    member = Member.objects.filter(user=user).first()
    return str(member.pk) if member else None


@api.get("/me", response=MeOut, auth=AUTHENTICATORS, summary="The signed-in user in the current club")
def me(request):
    """Who am I here: identity, roles in this club and the features the club has."""
    user, club = request.user, request.tenant
    evaluations_on = club.schema_name != get_public_schema_name() and request.features.has("evaluations")
    shop_on = club.schema_name != get_public_schema_name() and request.features.has("shop")
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
        "can_execute_evaluations": evaluations_on and access.can_execute_evaluations(user),
        "can_manage_evaluations": evaluations_on and access.can_manage_evaluations(user),
        "can_manage_roles": club.schema_name != get_public_schema_name() and access.can_manage_roles(user),
        "can_manage_referees": access.can_manage_referees(user),
        "can_publish_news": access.can_publish_news(user),
        "can_manage_forms": club.schema_name != get_public_schema_name() and access.can_manage_forms(user),
        "can_manage_shop_products": shop_on and access.can_manage_shop_products(user),
        "can_manage_shop_inventory": shop_on and access.can_manage_shop_inventory(user),
        "can_manage_shop_orders": shop_on and access.can_manage_shop_orders(user),
        "can_manage_shop_discounts": shop_on and access.can_manage_shop_discounts(user),
        "can_manage_shop_invoices": shop_on and access.can_manage_shop_invoices(user),
        "can_add_news": club.schema_name != get_public_schema_name() and access.can_add_news(user),
        "admin_group_ids": [] if club.schema_name == get_public_schema_name() else sorted(str(i) for i in access.admin_group_ids(user)),
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
