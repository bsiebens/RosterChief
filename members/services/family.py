"""Registering families: a parent (who needs a login) and a child (who doesn't),
linked to each other -- either together as a new family, or one at a time onto an
existing one.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from club.models import ClubMembership, Season
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


def _enrol(club, season, member, kind=ClubMembership.Kind.MEMBER, status=ClubMembership.StatusChoices.ACTIVE):
    """Sign a member up for the club's current season, if there is one. The
    implicit MEMBER role follows automatically (club/signals.py).

    ``kind=GUARDIAN`` attaches a parent to the club *as a parent* -- they hold
    the login and can be reached, but they aren't a member, owe no fee and are
    left out of every member list and count. A parent who also plays is enrolled
    as a MEMBER instead; the two are not exclusive of each other in the family
    graph, which records the parent relationship separately.

    ``status`` defaults to ACTIVE (every existing caller here is a staff-driven
    add, trusted to skip the Sign-up queue) -- registration.services.submission
    passes PENDING instead, so a public/mobile registration lands in that
    existing queue rather than going live unreviewed. Only meaningful for
    kind=MEMBER: a guardian is never queued (see approve_claim's own docstring
    for why that's a deliberate, separate decision).
    """
    if season is None:
        return None

    membership, _ = ClubMembership.objects.get_or_create(
        club=club,
        member=member,
        season=season,
        defaults={"kind": kind, "status": status if kind == ClubMembership.Kind.MEMBER else ClubMembership.StatusChoices.ACTIVE, "signed_up_at": timezone.localdate()},
    )
    if kind == ClubMembership.Kind.GUARDIAN:
        carry_guardians_forward(club, member, from_season=season)
    return membership


def carry_guardians_forward(club, member, *, from_season):
    """Give ``member`` a guardian row in every season of ``club`` at or after
    ``from_season``.

    A guardian's tie to the club isn't really seasonal -- it lasts as long as
    their child is there -- but it rides on ClubMembership so that everything
    already keying off that table (tenancy, groups, the event audience) keeps
    working. The cost of that is a row per season, and without this a parent
    would silently drop off the club at the next season boundary while their
    child stayed enrolled. Seasons are generated well ahead of time
    (club/services/seasons.py), so "every season from here on" is a real set,
    not just the next one. Idempotent.
    """
    later_seasons = Season.objects.filter(club=club, start_date__gte=from_season.start_date).exclude(pk=from_season.pk)
    for season in later_seasons:
        ClubMembership.objects.get_or_create(
            club=club,
            member=member,
            season=season,
            defaults={"kind": ClubMembership.Kind.GUARDIAN, "status": ClubMembership.StatusChoices.ACTIVE, "signed_up_at": timezone.localdate()},
        )


@transaction.atomic
def register_family(club, season, *, parent_email, parent_first_name, parent_last_name, child_first_name, child_last_name, child_date_of_birth=None, parent_is_member=False, child_status=ClubMembership.StatusChoices.ACTIVE):
    """Create a new family in one go: a parent (with a login) and a child
    (without one), linked to each other and signed up for ``season``.

    The child is always a member; the parent is a guardian unless
    ``parent_is_member`` says they play (or otherwise belong) in their own
    right. ``child_status`` -- see _enrol's own docstring -- lets
    registration.services.submission land the child's membership in the
    existing Sign-up queue (PENDING) instead of going active immediately;
    the parent's own guardian membership is unaffected either way.
    """
    parent = get_or_create_login_member(parent_email, parent_first_name, parent_last_name)
    child = Member.objects.create(first_name=child_first_name, last_name=child_last_name, date_of_birth=child_date_of_birth)

    family = Family.objects.create()
    FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)

    _enrol(club, season, parent, kind=ClubMembership.Kind.MEMBER if parent_is_member else ClubMembership.Kind.GUARDIAN, status=child_status)
    _enrol(club, season, child, status=child_status)

    return family


@transaction.atomic
def add_child_to_family(club, season, family, *, first_name, last_name, date_of_birth=None, status=ClubMembership.StatusChoices.ACTIVE):
    """A family that needs one more child registered -- a sibling joining, most
    often. ``status`` -- see _enrol's own docstring."""
    child = Member.objects.create(first_name=first_name, last_name=last_name, date_of_birth=date_of_birth)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
    _enrol(club, season, child, status=status)

    return child


@transaction.atomic
def add_parent_to_family(club, season, family, *, email="", first_name="", last_name="", parent_is_member=False, parent=None, status=ClubMembership.StatusChoices.ACTIVE):
    """A family that needs one more parent/guardian registered. A guardian
    unless ``parent_is_member`` says they belong to the club in their own right.

    ``parent`` lets a caller that already knows the Member (e.g.
    members.services.claims.approve_claim, once a claim carries a signed-in
    submitter) attach them directly instead of resolving ``email`` again --
    authoritative when the caller has it, and the only way to guarantee no
    second User/Member is ever created for the same person. ``status`` --
    see _enrol's own docstring -- only matters when ``parent_is_member``.
    """
    if parent is None:
        parent = get_or_create_login_member(email, first_name, last_name)
    # get_or_create, not create: re-adding a parent already on this family (a typo'd
    # re-submit, or a second claim that merged into it) must not trip unique_member_per_family.
    FamilyMembership.objects.get_or_create(family=family, member=parent, defaults={"role": FamilyMembership.FamilyRole.PARENT})
    _enrol(club, season, parent, kind=ClubMembership.Kind.MEMBER if parent_is_member else ClubMembership.Kind.GUARDIAN, status=status)

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
