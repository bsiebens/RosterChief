"""Platform access: who is staff or superuser on the public schema. Superusers manage this; staff cannot.

Rules (carried over from the previous app): superuser implies staff; nobody can strip their own access or their
own superuser flag; at least one superuser must always remain.
"""

from django.db import transaction
from django.db.models import Q
from django_tenants.utils import get_public_schema_name, schema_context
from tenant_users.permissions.models import UserTenantPermissions

from club.services import AccountError, UnverifiedEmailError, resolve_account


class PlatformAdminError(Exception):
    pass


def platform_admins():
    """Permission rows of everyone with platform access, with their profile loaded."""
    with schema_context(get_public_schema_name()):
        return list(UserTenantPermissions.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).select_related("profile").order_by("profile__email"))


def _perms_for(user) -> UserTenantPermissions:
    with schema_context(get_public_schema_name()):
        return UserTenantPermissions.objects.get(profile=user)


def check_access_change(actor, target, *, is_staff: bool, is_superuser: bool) -> None:
    has_access = is_staff or is_superuser
    if actor.pk == target.pk:
        if not has_access:
            raise PlatformAdminError("You cannot remove your own platform access.")
        if not is_superuser:
            raise PlatformAdminError("You cannot remove your own superuser rights.")
    if not is_superuser:
        with schema_context(get_public_schema_name()):
            others = UserTenantPermissions.objects.filter(is_superuser=True, profile__is_active=True).exclude(profile=target).exists()
        if _perms_for(target).is_superuser and not others:
            raise PlatformAdminError("At least one superuser must remain.")


@transaction.atomic
def set_platform_access(actor, target, *, is_staff: bool, is_superuser: bool) -> UserTenantPermissions:
    is_staff = is_staff or is_superuser  # superuser implies staff
    check_access_change(actor, target, is_staff=is_staff, is_superuser=is_superuser)
    with schema_context(get_public_schema_name()):
        perms = UserTenantPermissions.objects.get(profile=target)
        perms.is_staff, perms.is_superuser = is_staff, is_superuser
        perms.save(update_fields=["is_staff", "is_superuser"])
    return perms


def revoke_platform_access(actor, target) -> UserTenantPermissions:
    return set_platform_access(actor, target, is_staff=False, is_superuser=False)


@transaction.atomic
def grant_platform_access(email: str, *, is_superuser: bool, first_name: str = "", last_name: str = ""):
    """Give an account platform access. Returns ``(user, created)``.

    An existing account is only used if that exact address is verified; an unknown address gets a new login-less
    account (the caller sends the password setup email).
    """
    try:
        user, created = resolve_account(email, first_name=first_name, last_name=last_name)
    except UnverifiedEmailError:
        raise
    except AccountError as e:
        raise PlatformAdminError(str(e)) from e
    with schema_context(get_public_schema_name()):
        perms = UserTenantPermissions.objects.get(profile=user)
        perms.is_staff, perms.is_superuser = True, is_superuser
        perms.save(update_fields=["is_staff", "is_superuser"])
    return user, created
