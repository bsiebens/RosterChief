"""Fixing an accidental duplicate sign-up: a parent (or a plain typo)
registers someone who already has a Member row at this club, ending up with
two -- the real one, and a fresh throwaway created by this registration.
management.views.SignupLinkToMemberView is the one caller, from the Sign-up
page, where possible_duplicates below also suggests which existing member a
fresh sign-up most likely is -- an anonymous registration is never matched
to an existing Member automatically (registration.services.submission), so
this is where that link is made, by staff.
"""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership
from members.models import Member
from members.services.deletion import member_is_deletable
from teams.models import StaffAssignment, TeamMembership

from .cancellation import is_new_member


def link_to_existing_member(membership, target_member):
    """Re-points ``membership`` (currently on a duplicate Member row) onto
    ``target_member`` -- everything hanging off the ClubMembership itself
    (RegistrationDetails, MemberRequirementStatus, FeePayment, ...) follows
    automatically, since none of those change which row they reference, only
    that row's own ``member`` does. Any roster/staff placement already made
    for the duplicate this same season moves the same way.

    Raises ValidationError (not caught here -- the view's job) if
    ``target_member`` already has their own ClubMembership this season --
    nothing safe to merge fees/requests into. The duplicate Member is
    deleted afterwards if, now that its one ClubMembership has moved, it has
    no other history at this club (see cancellation.is_new_member) --
    otherwise it's left alone, since it turned out to be a real member with
    a real history, not a throwaway."""
    club, season, duplicate = membership.club, membership.season, membership.member
    if target_member.pk == duplicate.pk:
        raise ValidationError(_("Already the same member."))
    if ClubMembership.objects.filter(club=club, season=season, member=target_member).exists():
        raise ValidationError(_("%(member)s already has a registration this season.") % {"member": target_member})

    membership.member = target_member
    membership.save(update_fields=["member"])
    TeamMembership.objects.filter(team__club=club, member=duplicate, season=season).update(member=target_member)
    StaffAssignment.objects.filter(team__club=club, member=duplicate, season=season).update(member=target_member)

    # member_is_deletable on top of is_new_member: Member is global, so a
    # duplicate with a login or anything in another club is never deleted.
    if is_new_member(duplicate, club) and member_is_deletable(duplicate, ignoring=[membership]):
        duplicate.delete()

    return membership


def possible_duplicates(member, club):
    """Existing members of ``club`` who may be the same person as ``member``
    (typically a fresh sign-up) -- same email (own contact email or login
    email, case-insensitive), or same first name, last name and date of
    birth. Limited to the same candidates the Sign-up page's own "Link"
    picker offers (management.forms.SignupLinkMemberForm: anyone who has
    ever been a MEMBER-kind member of this club), so a hint is always
    something staff can act on, and never reveals a member of another club.
    Empty when ``member`` has neither an email nor a date of birth to match
    on."""
    match = Q()
    email = member.contact_email
    if email:
        match |= Q(email__iexact=email) | Q(user__email__iexact=email)
    if member.date_of_birth and member.first_name and member.last_name:
        match |= Q(first_name__iexact=member.first_name, last_name__iexact=member.last_name, date_of_birth=member.date_of_birth)
    if not match:
        return Member.objects.none()
    return Member.objects.filter(match, member_of__club=club, member_of__kind=ClubMembership.Kind.MEMBER).exclude(pk=member.pk).distinct().order_by("last_name", "first_name")
