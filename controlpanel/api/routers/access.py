"""Who am I, platform admin management (superusers) and the audit log."""

from uuid import UUID

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.pagination import paginate

from authorization.permissions import is_platform_superuser
from club.services import send_password_setup_email
from controlpanel import audit
from controlpanel.models import PlatformAuditLog
from controlpanel.services import platform_admins as svc

from ..auth import superuser_auth
from ..errors import ApiError
from ..idempotency import idempotent
from ..schemas import AuditLogOut, MeOut, PlatformAdminGrantIn, PlatformAdminOut, PlatformAdminUpdateIn

router = Router(tags=["access"])


@router.get("/me", response=MeOut, summary="The signed-in platform user and their level")
def me(request):
    user = request.user
    return MeOut(id=user.pk, email=user.email, first_name=user.first_name, last_name=user.last_name, level="superuser" if is_platform_superuser(user) else "staff")


@router.get("/admins", response=list[PlatformAdminOut], auth=superuser_auth, summary="Everyone with platform access")
@paginate
def list_admins(request):
    return [PlatformAdminOut.from_perms(p) for p in svc.platform_admins()]


@router.post("/admins", response={201: PlatformAdminOut}, auth=superuser_auth, summary="Grant platform access to an account")
@idempotent
def grant_admin(request, payload: PlatformAdminGrantIn):
    try:
        user, created = svc.grant_platform_access(payload.email, is_superuser=payload.is_superuser, first_name=payload.first_name, last_name=payload.last_name)
    except svc.UnverifiedEmailError as e:
        raise ApiError(409, "unverified_email", str(e)) from e
    except svc.PlatformAdminError as e:
        raise ApiError(422, "validation_error", str(e), {"first_name": [str(e)]} if "name" in str(e) else None) from e
    if created:
        send_password_setup_email(request, user)
    audit.record(request.user, "platform_admin.granted", target=user, data={"is_superuser": payload.is_superuser, "new_account": created})
    return 201, PlatformAdminOut.from_perms(svc._perms_for(user))


@router.patch("/admins/{user_id}", response=PlatformAdminOut, auth=superuser_auth, summary="Change someone's platform level")
def update_admin(request, user_id: UUID, payload: PlatformAdminUpdateIn):
    target = get_object_or_404(get_user_model(), pk=user_id)
    try:
        perms = svc.set_platform_access(request.user, target, is_staff=payload.is_staff, is_superuser=payload.is_superuser)
    except svc.PlatformAdminError as e:
        raise ApiError(409, "access_change_refused", str(e)) from e
    audit.record(request.user, "platform_admin.updated", target=target, data={"is_staff": perms.is_staff, "is_superuser": perms.is_superuser})
    return PlatformAdminOut.from_perms(perms)


@router.delete("/admins/{user_id}", response={204: None}, auth=superuser_auth, summary="Revoke platform access")
def revoke_admin(request, user_id: UUID):
    target = get_object_or_404(get_user_model(), pk=user_id)
    try:
        svc.revoke_platform_access(request.user, target)
    except svc.PlatformAdminError as e:
        raise ApiError(409, "access_change_refused", str(e)) from e
    audit.record(request.user, "platform_admin.revoked", target=target)
    return 204, None


class AuditFilters(Schema):
    action: str | None = None
    club_id: UUID | None = None
    actor_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None


@router.get("/audit-log", response=list[AuditLogOut], summary="Platform audit log, newest first")
@paginate
def audit_log(request, filters: AuditFilters = Query(...)):
    qs = PlatformAuditLog.objects.select_related("actor")
    for field, value in filters.dict(exclude_none=True).items():
        qs = qs.filter(**{field: value})
    return qs
