"""Fixing an accidental duplicate sign-up: a parent (or a plain typo)
registers someone who already has a Member row at this club, ending up with
two -- the real one, and a fresh throwaway created by this registration.
management.views.SignupLinkToMemberView is the one caller, from the Sign-up
page.
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership
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

    if is_new_member(duplicate, club):
        duplicate.delete()

    return membership
