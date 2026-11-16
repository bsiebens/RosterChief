"""User lookup: find any account and, for superusers, reset its 2FA / password or block it. PII, so superusers only."""

from typing import Literal
from uuid import UUID

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django_tenants.utils import get_public_schema_name
from ninja import Query, Router, Schema
from ninja.pagination import paginate

from controlpanel import audit
from controlpanel.models import AccountBlock, PlatformAuditLog
from controlpanel.services import accounts as svc

from ..auth import superuser_auth
from ..errors import ApiError
from ..idempotency import idempotent
from ..user_schemas import (
    AccountOut,
    BlockActionOut,
    BlockedOut,
    BlockIn,
    EmailAddressOut,
    ResetMfaIn,
    ResetMfaOut,
    ResetPasswordIn,
    ResetPasswordOut,
    SessionsOut,
    UnblockIn,
    UserActivityOut,
    UserClubOut,
    UserDetailOut,
    UserListOut,
)

router = Router(tags=["users"], auth=superuser_auth)


def _annotated_users():
    """Users with everything the list needs computed in the one query (no per-row queries)."""
    User = get_user_model()
    return User.objects.annotate(
        email_verified=Exists(EmailAddress.objects.filter(user=OuterRef("pk"), verified=True)),
        mfa_enabled=Exists(Authenticator.objects.filter(user=OuterRef("pk"))),
        perm_superuser=Coalesce(F("usertenantpermissions__is_superuser"), False),
        perm_staff=Coalesce(F("usertenantpermissions__is_staff"), False),
        joined_at=F("usertenantpermissions__created_at"),
        club_count=Count("tenants", filter=~Q(tenants__schema_name=get_public_schema_name()), distinct=True),
    )


class UserFilters(Schema):
    q: str | None = None
    status: Literal["active", "blocked", "all"] = "all"
    mfa: Literal["yes", "no", "any"] = "any"
    level: Literal["staff", "superuser", "none", "any"] = "any"


@router.get("/users", response=list[UserListOut], summary="Find user accounts")
@paginate
def list_users(request, filters: UserFilters = Query(...)):
    qs = _annotated_users()
    for term in (filters.q or "").split():
        qs = qs.filter(Q(email__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term))
    if filters.status != "all":
        qs = qs.filter(is_active=filters.status == "active")
    if filters.mfa != "any":
        qs = qs.filter(mfa_enabled=filters.mfa == "yes")
    if filters.level == "superuser":
        qs = qs.filter(perm_superuser=True)
    elif filters.level == "staff":
        qs = qs.filter(perm_staff=True, perm_superuser=False)
    elif filters.level == "none":
        qs = qs.filter(perm_staff=False, perm_superuser=False)
    return qs.order_by("last_name", "first_name", "email", "id")


def _level(user) -> str | None:
    from tenant_users.permissions.models import UserTenantPermissions

    perms = UserTenantPermissions.objects.filter(profile=user).first()
    if perms is None:
        return None
    return "superuser" if perms.is_superuser else "staff" if perms.is_staff else None


def _blocked_out(user) -> BlockedOut | None:
    if user.is_active:
        return None
    row = AccountBlock.objects.filter(user=user, unblocked_at__isnull=True).select_related("blocked_by").first()
    if row is None:  # inactive without a block record (deactivated some other way)
        return BlockedOut(is_blocked=True)
    return BlockedOut(is_blocked=True, blocked_at=row.blocked_at, blocked_by_email=row.blocked_by.email if row.blocked_by_id else None, reason=row.reason)


def _detail(user) -> UserDetailOut:
    from tenant_users.permissions.models import UserTenantPermissions

    perms = UserTenantPermissions.objects.filter(profile=user).first()
    activity = PlatformAuditLog.objects.filter(Q(target_type="user", target_id=str(user.pk)) | Q(actor=user)).select_related("actor").order_by("-created")[:20]
    return UserDetailOut(
        account=AccountOut(
            id=user.pk,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            date_joined=perms.created_at if perms else None,
            last_login=user.last_login,
            has_usable_password=user.has_usable_password(),
            platform_level=_level(user),
            emails=[EmailAddressOut(email=e.email, verified=e.verified, primary=e.primary) for e in svc.user_emails(user)],
        ),
        blocked=_blocked_out(user),
        mfa=svc.mfa_overview(user),
        clubs=[
            UserClubOut(
                club={"id": c["club"].pk, "name": c["club"].name, "slug": c["club"].slug, "archived": c["club"].archived_at is not None},
                roles=c["roles"],
                is_owner=c["is_owner"],
                is_admin=c["is_admin"],
            )
            for c in svc.user_clubs(user)
        ],
        sessions=SessionsOut(**svc.session_summary(user)),
        recent_activity=[UserActivityOut(id=a.pk, created=a.created, action=a.action, actor_email=a.actor.email if a.actor_id else None, target_type=a.target_type, target_id=a.target_id, data=a.data) for a in activity],
    )


def _target(user_id: UUID):
    return get_object_or_404(get_user_model(), pk=user_id)


@router.get("/users/{user_id}", response=UserDetailOut, summary="Everything about one account")
def user_detail(request, user_id: UUID):
    return _detail(_target(user_id))


@router.post("/users/{user_id}/reset-2fa", response={200: ResetMfaOut}, summary="Remove all of an account's two-factor methods")
@idempotent
def reset_2fa(request, user_id: UUID, payload: ResetMfaIn):
    target = _target(user_id)
    try:
        removed = svc.reset_mfa(request.user, target)
    except svc.UserActionRefused as e:
        raise ApiError(409, "user_action_refused", str(e)) from e
    notified = svc.notify_mfa_reset(target) if payload.notify else False
    audit.record(request.user, "user.mfa_reset", target=target, data={"removed": removed, "reason": payload.reason, "notified": notified})
    note = "Their password and sessions are untouched. Platform staff and superusers must enrol two-factor authentication again before they can use the platform API (outside local development)."
    return 200, ResetMfaOut(removed=removed, notified=notified, note=note)


@router.post("/users/{user_id}/reset-password", response={200: ResetPasswordOut}, summary="Email the account a password reset link")
@idempotent
def reset_password(request, user_id: UUID, payload: ResetPasswordIn):
    target = _target(user_id)
    try:
        svc.reset_password(request, request.user, target, invalidate_sessions=payload.invalidate_sessions, disable_current_password=payload.disable_current_password)
    except svc.UserActionRefused as e:
        raise ApiError(409, "user_action_refused", str(e)) from e
    audit.record(
        request.user,
        "user.password_reset",
        target=target,
        data={"reason": payload.reason, "invalidate_sessions": payload.invalidate_sessions, "disable_current_password": payload.disable_current_password},
    )
    parts = ["A password reset email was sent; the link lets the user choose a new password."]
    if payload.disable_current_password:
        parts.append("Their current password no longer works.")
    if payload.invalidate_sessions:
        parts.append("They were signed out everywhere.")
    return 200, ResetPasswordOut(sent_to=target.email, note=" ".join(parts))


@router.post("/users/{user_id}/block", response={200: BlockActionOut}, summary="Block an account and sign it out everywhere")
@idempotent
def block_user(request, user_id: UUID, payload: BlockIn):
    target = _target(user_id)
    try:
        _row, ended = svc.block(request.user, target, payload.reason)
    except svc.UserActionRefused as e:
        raise ApiError(409, "user_action_refused", str(e)) from e
    audit.record(request.user, "user.blocked", target=target, data={"reason": payload.reason, "sessions_ended": ended})
    target.refresh_from_db()
    return 200, BlockActionOut(user_id=target.pk, is_active=False, blocked=_blocked_out(target), sessions_ended=ended, note="The account can no longer sign in and was signed out everywhere.")


@router.post("/users/{user_id}/unblock", response={200: BlockActionOut}, summary="Let a blocked account sign in again")
@idempotent
def unblock_user(request, user_id: UUID, payload: UnblockIn):
    target = _target(user_id)
    try:
        svc.unblock(request.user, target, payload.reason)
    except svc.UserActionRefused as e:
        raise ApiError(409, "user_action_refused", str(e)) from e
    audit.record(request.user, "user.unblocked", target=target, data={"reason": payload.reason})
    return 200, BlockActionOut(user_id=target.pk, is_active=True, blocked=None, note="The account can sign in again with its existing password and two-factor methods.")
