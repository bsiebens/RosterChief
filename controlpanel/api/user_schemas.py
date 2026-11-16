"""Schemas for the user lookup endpoints (``/users``). None of them ever carries a secret."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from ninja import Schema
from pydantic import StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
PlatformLevel = Literal["staff", "superuser"] | None


class UserListOut(Schema):
    id: UUID
    email: str
    first_name: str
    last_name: str
    is_active: bool
    last_login: datetime | None = None
    date_joined: datetime | None = None  # when the account was created on the platform (public-schema permissions row)
    email_verified: bool
    mfa_enabled: bool
    platform_level: PlatformLevel = None
    club_count: int
    blocked: bool  # == not is_active

    @staticmethod
    def resolve_platform_level(obj):
        if getattr(obj, "perm_superuser", False):
            return "superuser"
        return "staff" if getattr(obj, "perm_staff", False) else None

    @staticmethod
    def resolve_blocked(obj):
        return not obj.is_active

    @staticmethod
    def resolve_date_joined(obj):
        return getattr(obj, "joined_at", None)


class EmailAddressOut(Schema):
    email: str
    verified: bool
    primary: bool


class AccountOut(Schema):
    id: UUID
    email: str
    first_name: str
    last_name: str
    is_active: bool
    date_joined: datetime | None = None
    last_login: datetime | None = None
    has_usable_password: bool
    platform_level: PlatformLevel = None
    emails: list[EmailAddressOut]


class BlockedOut(Schema):
    is_blocked: bool
    blocked_at: datetime | None = None
    blocked_by_email: str | None = None
    reason: str | None = None


class AuthenticatorOut(Schema):
    type: Literal["totp", "webauthn", "recovery_codes"]
    name: str | None = None  # WebAuthn key name only
    created_at: datetime
    last_used_at: datetime | None = None
    unused_codes: int | None = None  # recovery codes only: how many are left (never the codes)


class MfaOut(Schema):
    enabled: bool
    authenticators: list[AuthenticatorOut]


class UserClubRef(Schema):
    id: UUID
    name: str
    slug: str
    archived: bool


class UserClubOut(Schema):
    club: UserClubRef
    roles: list[str]
    is_owner: bool
    is_admin: bool


class SessionsOut(Schema):
    active_count: int
    last_seen: datetime | None = None  # estimate, see controlpanel.services.accounts.session_summary
    truncated: bool = False  # True when the platform has more sessions than the bounded scan inspects


class UserActivityOut(Schema):
    id: UUID
    created: datetime
    action: str
    actor_email: str | None = None
    target_type: str
    target_id: str
    data: dict


class UserDetailOut(Schema):
    account: AccountOut
    blocked: BlockedOut | None = None
    mfa: MfaOut
    clubs: list[UserClubOut]
    sessions: SessionsOut
    recent_activity: list[UserActivityOut]


class ResetMfaIn(Schema):
    reason: Reason
    notify: bool = True


class ResetMfaOut(Schema):
    removed: list[str]
    notified: bool
    note: str


class ResetPasswordIn(Schema):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    invalidate_sessions: bool = True
    disable_current_password: bool = False


class ResetPasswordOut(Schema):
    sent_to: str
    note: str


class BlockIn(Schema):
    reason: Reason


class UnblockIn(Schema):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class BlockActionOut(Schema):
    user_id: UUID
    is_active: bool
    blocked: BlockedOut | None = None
    sessions_ended: int | None = None
    note: str
