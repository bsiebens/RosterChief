"""Overview API (``/api/v1/overview``): the numbers behind the home page. Everyone who got into the management app
sees the member, team and event numbers; fee status and the renewal rate need ``members.manage_members``."""

from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.schemas_overview import OverviewOut
from members.services import access, overview

router = Router()


@router.get("", response=OverviewOut, summary="Numbers for the overview page", operation_id="club_overview")
def club_overview(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    return overview.overview(include_fees=access.can_manage_members(request.user))
