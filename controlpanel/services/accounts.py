"""Resolving the account a platform grant (club admin, platform access) lands on.

A grant is addressed to an email. It may only attach to an existing account when
that account has *verified* the address: otherwise whoever typed the address into
their account (without being able to receive mail there) would inherit rights
meant for its real owner.
"""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

User = get_user_model()


class UnverifiedEmailError(Exception):
    """An account already uses the address, but has not verified it."""


def account_for_grant(email):
    """The existing account a grant for ``email`` may attach to, or None when no
    account uses that address yet (the caller then creates one). Raises
    UnverifiedEmailError when an account uses it without having verified it."""
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return None
    if not EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists():
        raise UnverifiedEmailError(_("An account already uses %(email)s, but that address has not been confirmed yet. Ask them to sign in and confirm it, then try again.") % {"email": email})
    return user


def create_account_for_grant(email):
    """A new, active account for ``email`` without a usable password: the
    invitee sets one through password reset, and confirms the address on their
    first sign-in (ACCOUNT_EMAIL_VERIFICATION = "mandatory")."""
    user = User(email=email, is_active=True)
    user.set_unusable_password()
    user.save()
    return user
