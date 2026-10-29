"""Management roles (``/manage/roles``): who holds which club-wide role.

Club admins only, as on the web (``ClubAdminRequiredMixin``). Granting uses the same form
(``ClubRoleAssignForm``) and service (``club.services.roles``) as the roles screen: admin / editor /
member admin set the member's single ClubRole; shop admin, referee admin and evaluation manager are
additive grants. A member admin can never reach these endpoints, so nobody can grant themselves admin.
"""

import uuid

from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import roles as s
from club.models import ClubRole, EvaluationManager, RefereeManager, ShopManager
from club.services.access import is_club_admin
from club.services.roles import EVALUATION_MANAGER, REFEREE_ADMIN, SHOP_ADMIN, grant_role, revoke_grant, revoke_role
from management.forms import ClubRoleAssignForm

router = Router(tags=["panel: roles"])

PREFIX = "/manage/roles"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _admin(request):
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _person(member) -> dict:
    return {"id": member.pk, "name": str(member)}


def _holder(row, role=None, label=None) -> dict:
    return {"id": row.pk, "member": _person(row.member), "role": role, "role_label": label}


@router.get(PREFIX, response={200: s.RolesOut, **ERRORS}, summary="Who holds which role")
def list_roles(request):
    """Granted ClubRole holders and the three additive grants, by name. Club admins."""
    club = _admin(request)
    roles = ClubRole.objects.filter(club=club).exclude(role=ClubRole.Roles.MEMBER).select_related("member")
    return {
        "roles": [_holder(row, row.role, row.get_role_display()) for row in roles],
        "shop_admins": [_holder(row) for row in ShopManager.objects.filter(club=club).select_related("member")],
        "referee_admins": [_holder(row) for row in RefereeManager.objects.filter(club=club).select_related("member")],
        "evaluation_managers": [_holder(row) for row in EvaluationManager.objects.filter(club=club).select_related("member")],
    }


@router.post(PREFIX, response={200: s.RoleGrantOut, 201: s.RoleGrantOut, **ERRORS, **INVALID}, summary="Grant a role")
def grant(request, payload: s.RoleGrantIn):
    """`201` when granted, `200` when the member already held it (an `admin`/`editor`/`member_admin` grant replaces the member's current role). Honours `Idempotency-Key`. Club admins."""
    club = _admin(request)

    def work():
        form = ClubRoleAssignForm(post_data(payload.dict(), single={"member_id": "member"}), club=club)
        if not form.is_valid():
            raise form_error(form, {"member": "member_id"})
        result = grant_role(club, form.cleaned_data["member"], form.cleaned_data["role"])
        row_id = None
        if result.role is not None:
            row_id = result.role.pk
        else:
            model = {SHOP_ADMIN: ShopManager, REFEREE_ADMIN: RefereeManager, EVALUATION_MANAGER: EvaluationManager}[result.kind]
            row_id = model.objects.get(club=club, member=result.member).pk
        body = {"id": row_id, "member": _person(result.member), "role": result.kind, "role_label": result.role_label or None, "created": result.created}
        return (201 if result.created else 200), body

    return idempotent(request, work)


def _revoke(request, call):
    club = _admin(request)
    try:
        call(club)
    except ObjectDoesNotExist:
        raise ApiError(404, "not_found", _("Not found.")) from None
    return 204, None


@router.delete(PREFIX + "/{uuid:role_id}", response={204: None, **ERRORS}, summary="Revoke a ClubRole")
def revoke(request, role_id: uuid.UUID):
    """Removes the member's granted role. Club admins."""
    return _revoke(request, lambda club: revoke_role(club, role_id))


@router.delete(PREFIX + "/shop-admins/{uuid:grant_id}", response={204: None, **ERRORS}, summary="Revoke a shop admin grant")
def revoke_shop_admin(request, grant_id: uuid.UUID):
    return _revoke(request, lambda club: revoke_grant(club, SHOP_ADMIN, grant_id))


@router.delete(PREFIX + "/referee-admins/{uuid:grant_id}", response={204: None, **ERRORS}, summary="Revoke a referee admin grant")
def revoke_referee_admin(request, grant_id: uuid.UUID):
    return _revoke(request, lambda club: revoke_grant(club, REFEREE_ADMIN, grant_id))


@router.delete(PREFIX + "/evaluation-managers/{uuid:grant_id}", response={204: None, **ERRORS}, summary="Revoke an evaluation manager grant")
def revoke_evaluation_manager(request, grant_id: uuid.UUID):
    return _revoke(request, lambda club: revoke_grant(club, EVALUATION_MANAGER, grant_id))
