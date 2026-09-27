"""Granting and revoking club-admin rights from the platform panel."""

from django.db import transaction

from club.models import ClubRole
from members.models import Member

from .accounts import account_for_grant, create_account_for_grant


def find_member_by_email(email):
    """The Member behind a login email, if that account exists at all."""
    return Member.objects.filter(user__email__iexact=email).first()


@transaction.atomic
def grant_club_admin(club, email, first_name="", last_name=""):
    """Make the holder of ``email`` an ADMIN of ``club``, creating them if new.

    A ClubRole hangs off a Member, and a Member optionally links to a User — so
    an admin who has never existed needs both. The account is created without a
    usable password; they set one via the password-reset flow. An existing
    account is only used when it has verified ``email``; otherwise this raises
    UnverifiedEmailError and grants nothing.
    """
    email = email.lower()
    user = account_for_grant(email) or create_account_for_grant(email)

    member, _ = Member.objects.get_or_create(
        user=user,
        defaults={"first_name": first_name, "last_name": last_name},
    )

    # One role per member per club, so promote rather than add a second row.
    role, created_role = ClubRole.objects.get_or_create(club=club, member=member, defaults={"role": ClubRole.Roles.ADMIN})
    if not created_role and role.role != ClubRole.Roles.ADMIN:
        role.role = ClubRole.Roles.ADMIN
        role.save(update_fields=["role"])

    return role


def revoke_club_admin(role):
    """Remove admin rights. The membership-status sync never re-adds ADMIN."""
    role.delete()
