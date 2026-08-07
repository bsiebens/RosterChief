"""Granting and revoking platform access (is_staff / is_superuser).

The guardrails matter more than the plumbing here: it must be impossible to lock
the platform out of itself. Two rules are enforced for every change:

* you cannot strip your **own** access (you would lose the panel mid-click);
* the **last superuser** can never be demoted, or nobody could administer the
  platform again without shell access.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

User = get_user_model()


class PlatformAdminError(Exception):
    """A change that would leave the platform unadministrable."""


def platform_admins():
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).order_by("email")


def is_last_superuser(user) -> bool:
    return user.is_superuser and not User.objects.filter(is_superuser=True).exclude(pk=user.pk).exists()


def check_access_change(actor, user, *, is_staff: bool, is_superuser: bool) -> None:
    """Raise PlatformAdminError if this change would lock someone out."""
    losing_access = not (is_staff or is_superuser)

    if actor.pk == user.pk and losing_access:
        raise PlatformAdminError("You cannot remove your own platform access.")

    if actor.pk == user.pk and user.is_superuser and not is_superuser:
        raise PlatformAdminError("You cannot remove your own superuser rights.")

    if user.is_superuser and not is_superuser and is_last_superuser(user):
        raise PlatformAdminError("At least one superuser must remain.")


@transaction.atomic
def set_platform_access(actor, user, *, is_staff: bool, is_superuser: bool):
    check_access_change(actor, user, is_staff=is_staff, is_superuser=is_superuser)

    # A superuser without is_staff cannot reach the panel, which is a confusing
    # half-state; superuser implies staff.
    user.is_staff = is_staff or is_superuser
    user.is_superuser = is_superuser
    user.save(update_fields=["is_staff", "is_superuser"])
    return user


def revoke_platform_access(actor, user):
    return set_platform_access(actor, user, is_staff=False, is_superuser=False)


@transaction.atomic
def grant_platform_access(email, *, is_superuser: bool = False):
    """Give ``email`` platform access, creating the account if it is new.

    New accounts get an unusable password — they set one through the password
    reset flow — and, being staff, must enrol a second factor before they can
    sign in at all.
    """
    email = email.lower()
    user, created = User.objects.get_or_create(email=email, defaults={"is_active": True})
    if created:
        user.set_unusable_password()

    user.is_staff = True
    user.is_superuser = is_superuser
    user.save()
    return user
