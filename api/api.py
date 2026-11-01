from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from ninja import NinjaAPI
from django_tenants.utils import get_public_schema_name

from .auth import AUTHENTICATORS
from .errors import register_error_handlers
from .schemas import MeOut

api = NinjaAPI(
    title="RosterChief API",
    version="1",
    description="Authentication (login, MFA, passkeys) is served by allauth headless under /_allauth/.",
    auth=AUTHENTICATORS,
    urls_namespace="api",
    docs_url="/docs" if settings.DEBUG else None,
)
register_error_handlers(api)


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
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "club": {"slug": club.slug, "name": club.name, "is_platform": club.schema_name == get_public_schema_name()},
        "roles": roles,
        "features": sorted(request.features.enabled_codes()),
    }
