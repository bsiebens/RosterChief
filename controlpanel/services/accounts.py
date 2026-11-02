"""Platform-side account control: find sessions, reset 2FA / password, block and unblock an account.

Nothing here ever reads or returns secrets (passwords, tokens, TOTP seeds, WebAuthn credentials, recovery codes).
"""

import datetime

from allauth.account.models import EmailAddress
from allauth.mfa import app_settings as mfa_settings
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.mail import EmailMessage
from django.db import ProgrammingError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from tenant_users.permissions.models import UserTenantPermissions

from authorization.catalog import ADMIN_CODES, OWNER_SLUG
from club.services import send_password_setup_email
from controlpanel.models import AccountBlock
from rosterchief.mail import send_message

# Counting sessions decodes every unexpired row of django_session (the payload is signed, not indexed by user), so
# the detail view scans at most this many rows and reports ``truncated`` beyond it. Deleting never stops early.
SESSION_SCAN_LIMIT = 50_000


class UserActionRefused(Exception):
    """The action is not allowed for this account right now; the message is shown to the operator."""


# --- sessions ------------------------------------------------------------------------------------------------------


def _session_rows(user, *, include_expired: bool, limit: int | None, state: dict | None = None):
    """Yield ``(session_key, expire_date)`` of the sessions that belong to ``user``; stops after ``limit`` rows read.

    Cost: one streamed pass over ``django_session`` (unexpired rows only unless ``include_expired``), decoding each
    payload (HMAC check + JSON). O(number of sessions on the platform), not O(user's sessions). ``state["truncated"]``
    is set when the limit stopped the scan before the end.
    """
    store, wanted = SessionStore(), str(user.pk)
    with schema_context(get_public_schema_name()):
        qs = Session.objects.all() if include_expired else Session.objects.filter(expire_date__gt=timezone.now())
        scanned = 0
        for row in qs.only("session_key", "session_data", "expire_date").iterator(chunk_size=2000):
            if limit is not None and scanned >= limit:
                if state is not None:
                    state["truncated"] = True
                return
            scanned += 1
            if store.decode(row.session_data).get(SESSION_KEY) == wanted:
                yield row.session_key, row.expire_date


def session_summary(user) -> dict:
    """``{active_count, last_seen, truncated}``. ``last_seen`` is estimated: django only stores each session's expiry
    (refreshed whenever the session is written), so the newest ``expire_date - SESSION_COOKIE_AGE`` is when it was last written."""
    state = {"truncated": False}
    expiries = [expire for _key, expire in _session_rows(user, include_expired=False, limit=SESSION_SCAN_LIMIT, state=state)]
    last_seen = max(expiries) - datetime.timedelta(seconds=settings.SESSION_COOKIE_AGE) if expiries else None
    return {"active_count": len(expiries), "last_seen": last_seen, "truncated": state["truncated"]}


def delete_user_sessions(user) -> int:
    """Delete every session of ``user`` (headless X-Session-Tokens are session keys, so they die with them)."""
    keys = [key for key, _ in _session_rows(user, include_expired=True, limit=None)]
    with schema_context(get_public_schema_name()):
        for i in range(0, len(keys), 500):
            Session.objects.filter(session_key__in=keys[i : i + 500]).delete()
    return len(keys)


# --- clubs ---------------------------------------------------------------------------------------------------------


def user_clubs(user) -> list[dict]:
    """The clubs (not the platform itself) the user belongs to, with their roles there."""
    out = []
    for club in user.tenants.exclude(schema_name=get_public_schema_name()).order_by("name"):
        roles, is_admin = [], False
        try:
            with transaction.atomic(), schema_context(club.schema_name):
                perms = UserTenantPermissions.objects.filter(profile=user).first()
                if perms is not None:
                    for group in perms.groups.select_related("role").prefetch_related("permissions__content_type"):
                        roles.append(group.name)
                        codes = {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()}
                        role = getattr(group, "role", None)
                        is_admin = is_admin or bool(ADMIN_CODES & codes) or (role is not None and role.slug == OWNER_SLUG)
        except ProgrammingError:  # the club's schema is missing; show the club without roles
            roles = []
        is_owner = club.owner_id == user.pk
        out.append({"club": club, "roles": sorted(roles), "is_owner": is_owner, "is_admin": is_admin or is_owner})
    return out


def mfa_overview(user) -> dict:
    """Which authenticators the user has. Never includes secrets, WebAuthn credentials or the codes themselves."""
    items = []
    for a in Authenticator.objects.filter(user=user).order_by("created_at"):
        name = unused = None
        if a.type == Authenticator.Type.WEBAUTHN:
            name = a.data.get("name")
        elif a.type == Authenticator.Type.RECOVERY_CODES:
            migrated = a.data.get("migrated_codes")
            if migrated is not None:
                unused = len(migrated)
            else:
                unused = max(mfa_settings.RECOVERY_CODE_COUNT - bin(int(a.data.get("used_mask", 0))).count("1"), 0)
        items.append({"type": a.type, "name": name, "created_at": a.created_at, "last_used_at": a.last_used_at, "unused_codes": unused})
    return {"enabled": bool(items), "authenticators": items}


def user_emails(user) -> list[EmailAddress]:
    return list(EmailAddress.objects.filter(user=user).order_by("-primary", "email"))


# --- actions -------------------------------------------------------------------------------------------------------


def _lock(target):
    return get_user_model().objects.select_for_update().get(pk=target.pk)


@transaction.atomic
def reset_mfa(actor, target) -> list[str]:
    """Delete all of the target's authenticators. Returns the removed types."""
    if actor.pk == target.pk:
        raise UserActionRefused("You cannot reset your own two-factor authentication here; use your account security page.")
    target = _lock(target)
    authenticators = list(Authenticator.objects.filter(user=target))
    if not authenticators:
        raise UserActionRefused("Nothing to reset: this account has no two-factor authentication.")
    removed = sorted({a.type for a in authenticators})
    Authenticator.objects.filter(user=target).delete()
    return removed


def notify_mfa_reset(user) -> bool:
    """Tell the user their 2FA was reset. Contains no links or secrets; a failure is not fatal."""
    try:
        body = (
            f"Hello {user.first_name or user.email},\n\n"
            "Your two-factor authentication on RosterChief was reset by a platform administrator. "
            "You can sign in with your password as before; please set up two-factor authentication again "
            "from your account security settings.\n\n"
            "If you did not expect this, contact RosterChief support.\n"
        )
        return send_message(EmailMessage(subject="Your two-factor authentication was reset", body=body, to=[user.email]), exempt=True)
    except Exception:
        return False


def reset_password(request, actor, target, *, invalidate_sessions: bool, disable_current_password: bool) -> None:
    """Email a password reset link (never sets a password). The token is derived from the password hash, so the
    old password is disabled *before* the mail is generated; if sending fails nothing is changed."""
    if actor.pk == target.pk:
        raise UserActionRefused("You cannot reset your own password here; use your account security page.")
    if not target.is_active:
        raise UserActionRefused("This account is blocked, so no password reset email can be sent. Unblock it first.")
    if not EmailAddress.objects.filter(user=target, email__iexact=target.email).exists():
        raise UserActionRefused("This account's email address is not registered for sign-in, so no reset email can be sent.")
    with transaction.atomic():
        target = _lock(target)
        if disable_current_password:
            target.set_unusable_password()
            target.save(update_fields=["password"])
        send_password_setup_email(request, target)
    if invalidate_sessions:
        delete_user_sessions(target)


def _only_active_superuser(target) -> bool:
    with schema_context(get_public_schema_name()):
        qs = UserTenantPermissions.objects.select_for_update().filter(is_superuser=True, profile__is_active=True)
        ids = list(qs.values_list("profile_id", flat=True))
    return ids == [target.pk]


@transaction.atomic
def block(actor, target, reason: str) -> tuple[AccountBlock, int]:
    """Returns the new block row and the number of sessions ended."""
    if actor.pk == target.pk:
        raise UserActionRefused("You cannot block your own account.")
    target = _lock(target)
    if not target.is_active:
        raise UserActionRefused("This account is already blocked.")
    if _only_active_superuser(target):
        raise UserActionRefused("This is the last active superuser; blocking it would lock everyone out of the platform.")
    target.is_active = False
    target.save(update_fields=["is_active"])
    row = AccountBlock.objects.create(user=target, blocked_by=actor, reason=reason)
    return row, delete_user_sessions(target)


@transaction.atomic
def unblock(actor, target, reason: str = "") -> AccountBlock | None:
    """Re-activates the account and closes the open block row."""
    target = _lock(target)
    if target.is_active:
        raise UserActionRefused("This account is not blocked.")
    target.is_active = True
    target.save(update_fields=["is_active"])
    row = AccountBlock.objects.filter(user=target, unblocked_at__isnull=True).first()
    if row is not None:
        row.unblocked_at, row.unblocked_by, row.unblock_reason = timezone.now(), actor, reason
        row.save(update_fields=["unblocked_at", "unblocked_by", "unblock_reason", "modified"])
    return row
