"""Registering families: a parent (who needs a login) and a child (who doesn't),
linked to each other -- either together as a new family, or one at a time onto an
existing one.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from club.models import ClubMembership
from members.models import Family, FamilyMembership, Member

User = get_user_model()


def find_member_by_email(email):
    """The Member behind a login email, if that account exists at all."""
    return Member.objects.filter(user__email__iexact=email).first()


def get_or_create_login_user(email):
    """Find or create the User behind ``email`` -- no usable password, they set
    one via the reset-link flow the first time we see them. Mirrors
    controlpanel.services.admins.grant_club_admin."""
    email = email.lower()
    user, user_created = User.objects.get_or_create(email=email, defaults={"is_active": True})
    if user_created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user, user_created


def get_or_create_login_member(email, first_name="", last_name=""):
    """Find or create the Member behind ``email``, creating a User the first time
    we see them."""
    user, _ = get_or_create_login_user(email)
    member, _ = Member.objects.get_or_create(user=user, defaults={"first_name": first_name, "last_name": last_name})
    return member


def grant_login(member, email):
    """Give a login-less member (a child old enough to need their own account, most
    often) the ability to sign in -- a new User with no usable password, same as
    get_or_create_login_member; they set one via the reset-link flow. Unlike that
    function, this attaches the login to a specific, already-existing Member rather
    than finding-or-creating one -- the caller's form has already checked the email
    isn't already in use."""
    user = User.objects.create(email=email.lower(), is_active=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])

    member.user = user
    member.save(update_fields=["user"])
    return user


def _enrol(club, season, member):
    """Sign a member up for the club's current season, if there is one. The
    implicit MEMBER role follows automatically (club/signals.py)."""
    if season is None:
        return None

    membership, _ = ClubMembership.objects.get_or_create(
        club=club,
        member=member,
        season=season,
        defaults={"status": ClubMembership.StatusChoices.ACTIVE, "signed_up_at": timezone.localdate()},
    )
    return membership


@transaction.atomic
def register_family(club, season, *, parent_email, parent_first_name, parent_last_name, child_first_name, child_last_name, child_date_of_birth=None):
    """Create a new family in one go: a parent (with a login) and a child
    (without one), linked to each other and signed up for ``season``."""
    parent = get_or_create_login_member(parent_email, parent_first_name, parent_last_name)
    child = Member.objects.create(first_name=child_first_name, last_name=child_last_name, date_of_birth=child_date_of_birth)

    family = Family.objects.create()
    FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)

    _enrol(club, season, parent)
    _enrol(club, season, child)

    return family


@transaction.atomic
def add_child_to_family(club, season, family, *, first_name, last_name, date_of_birth=None):
    """A family that needs one more child registered -- a sibling joining, most often."""
    child = Member.objects.create(first_name=first_name, last_name=last_name, date_of_birth=date_of_birth)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
    _enrol(club, season, child)

    return child


@transaction.atomic
def add_parent_to_family(club, season, family, *, email, first_name="", last_name=""):
    """A family that needs one more parent/guardian registered."""
    parent = get_or_create_login_member(email, first_name, last_name)
    # get_or_create, not create: re-adding an email already on this family (a typo'd
    # re-submit, say) must not trip unique_member_per_family.
    FamilyMembership.objects.get_or_create(family=family, member=parent, defaults={"role": FamilyMembership.FamilyRole.PARENT})
    _enrol(club, season, parent)

    return parent


def attach_to_family(member, *, role, family=None):
    """Link a standalone member into a family -- a new one, or an existing one they
    turn out to belong to (e.g. a second parent already registered separately)."""
    if family is None:
        family = Family.objects.create()

    FamilyMembership.objects.get_or_create(family=family, member=member, defaults={"role": role})
    return family


@transaction.atomic
def detach_from_family(member, family):
    """Remove member from one specific family -- they may still belong to others.
    An empty family (this was its last member) is cleaned up same as before."""
    FamilyMembership.objects.filter(member=member, family=family).delete()
    if not family.memberships.exists():
        family.delete()
