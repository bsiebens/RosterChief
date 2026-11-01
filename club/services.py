from allauth.account.forms import ResetPasswordForm
from allauth.account.models import EmailAddress
from allauth.core import context
from django.contrib.auth import get_user_model
from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

from authorization.models import Role
from authorization.services import assign_role
from members.models import Member

from .models import Club


def mark_email_verified(user) -> None:
    """Mark the user's login email as verified and primary, both on the user and in allauth."""
    with schema_context(get_public_schema_name()):
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])
        EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"primary": True, "verified": True})


@transaction.atomic
def create_account(email: str, first_name: str = "", last_name: str = ""):
    """Create a login-less account (unusable password). The user sets a password via the reset flow.

    Marking the address verified is safe: the only way to set a password is the emailed reset link.
    """
    with schema_context(get_public_schema_name()):
        user = get_user_model().objects.create_user(email=email, first_name=first_name, last_name=last_name)
        mark_email_verified(user)
    return user


def create_member(club: Club, first_name: str, last_name: str, email: str = "") -> Member:
    """Add a person to the club's roster without a login."""
    with schema_context(club.schema_name):
        return Member.objects.create(first_name=first_name, last_name=last_name, email=email)


@transaction.atomic
def grant_login(club: Club, member: Member, *, role_slug: str = "member"):
    """Give an existing member access to the club, using ``member.email`` as the login.

    Returns ``(user, user_created)``. Only send the password setup email when ``user_created`` is True;
    an existing user already has credentials and just gains access to another club.
    """
    if not member.email:
        raise ValueError("Member needs an email address to receive a login.")
    if member.user_id:
        raise ValueError("Member already has a login.")

    with schema_context(get_public_schema_name()):
        user = get_user_model().objects.filter(email__iexact=member.email).first()
    user_created = user is None
    if user_created:
        user = create_account(member.email, member.first_name, member.last_name)

    club.add_user(user)  # public: user.tenants link; club schema: UserTenantPermissions (raises ExistsError if already in)

    with schema_context(club.schema_name):
        assign_role(user, Role.objects.get(slug=role_slug))
        member.user = user
        member.save(update_fields=["user", "modified"])
    return user, user_created


@transaction.atomic
def create_member_for_club(club: Club, email: str, first_name: str, last_name: str, *, role_slug: str = "member"):
    """Create a member and give them a login in one step. Returns ``(member, user, user_created)``."""
    member = create_member(club, first_name, last_name, email)
    user, user_created = grant_login(club, member, role_slug=role_slug)
    return member, user, user_created


def send_password_setup_email(request, user) -> None:
    """Email the user a password reset link (used as the account invite)."""
    form = ResetPasswordForm(data={"email": user.email})
    if not form.is_valid():
        raise ValueError(form.errors.as_text())
    with context.request_context(request):
        form.save(request)
