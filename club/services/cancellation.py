"""Cancelling one person's sign-up (management.views.SignupCancelView, the
public registration status page's own RegistrationCancelView).

Two different things can happen, depending on whether this club has ever
seen this member before this season:

- A returning member (has at least one other ClubMembership at this club,
  any season) gets a soft cancel -- ClubMembership.status flips to
  CANCELLED, everything else (fee history, checklist) stays exactly as it
  was, for the record. The one exception is a roster/staff spot this
  membership's *own* registration(s) auto-placed at confirm time
  (registration.models.RegistrationDetails.resulting_team_membership/
  resulting_staff_assignment, set by management.views.
  RegistrationInvoiceReviewView) -- those are removed, since a cancelled
  registration has no business still holding a team spot it only got by
  being confirmed. A spot placed some other way entirely (e.g. straight
  from the roster page, no registration involved) is left untouched -- this
  only ever touches what *this membership's own* registration(s) created.
- Someone who only exists because of this one sign-up has nothing worth
  keeping once it's withdrawn -- their Member row is deleted outright
  (cascading the ClubMembership itself, its RegistrationDetails,
  MemberRequirementStatus, FeePayment, and any roster/staff placement),
  rather than leaving a permanently-empty, cancelled shell in the member
  database. See registration.models.RegistrationDetails' own docstring on
  why registering is always "create/find the Member, then attach a
  ClubMembership" -- this is the reverse of that for the one-shot case.

  Member is global, though, so "only exists because of this one sign-up" is
  decided by members.services.deletion.member_is_deletable, never by this
  club's rows alone: a member with a login, or with anything in another
  club, is never deleted here. When such a member is new to *this* club,
  only this club's own rows for the sign-up are removed instead (the
  membership with everything cascading from it, its roster/staff spots for
  that season, and a child/solo row in this club's family), leaving the
  Member and everything they have elsewhere untouched.
"""

from django.db import transaction

from club.models import ClubMembership
from members.models import FamilyMembership
from members.services.deletion import member_is_deletable
from members.services.family import detach_from_family
from teams.models import StaffAssignment, TeamMembership


def is_new_member(member, club) -> bool:
    """Whether ``member`` has never had a ClubMembership at ``club`` before
    -- the "born this season, for this one sign-up" case cancel_membership
    cleans up entirely rather than leaving behind an empty record."""
    return not ClubMembership.objects.filter(club=club, member=member).exists()


@transaction.atomic
def cancel_membership(membership):
    """Cancel ``membership``. Returns True if the Member itself was deleted,
    False otherwise:

    - a returning member of this club (another ClubMembership here) is soft-
      cancelled -- status CANCELLED, plus removal of any team/staff spot this
      membership's own registration(s) auto-placed (see this module's own
      docstring);
    - a member who is new to this club and has nothing else anywhere
      (members.services.deletion.member_is_deletable) is deleted outright;
    - a member who is new to this club but is kept (a login, or history
      elsewhere) loses only this club's rows for this sign-up."""
    club, member = membership.club, membership.member
    if ClubMembership.objects.filter(club=club, member=member).exclude(pk=membership.pk).exists():
        _remove_registration_placements(membership)
        membership.status = ClubMembership.StatusChoices.CANCELLED
        membership.save(update_fields=["status"])
        return False

    if member_is_deletable(member, ignoring=[membership]):
        member.delete()
        return True

    _remove_club_rows(membership)
    return False


def _remove_registration_placements(membership):
    for details in membership.registration_details.exclude(resulting_team_membership=None):
        details.resulting_team_membership.delete()
    for details in membership.registration_details.exclude(resulting_staff_assignment=None):
        details.resulting_staff_assignment.delete()


def _remove_club_rows(membership):
    """What deleting the Member would have removed *at this club* -- the
    membership (cascading its RegistrationDetails, requirement statuses and
    fee payments), this season's roster/staff spots here, and this club's
    family rows unless other family members still rely on them as a
    parent/guardian. Nothing outside this club is touched."""
    club, member, season = membership.club, membership.member, membership.season
    TeamMembership.objects.filter(team__club=club, member=member, season=season).delete()
    StaffAssignment.objects.filter(team__club=club, member=member, season=season).delete()
    membership.delete()
    for family_row in FamilyMembership.objects.filter(member=member, family__club=club).select_related("family"):
        is_relied_on = family_row.role != FamilyMembership.FamilyRole.CHILD and family_row.family.memberships.exclude(member=member).exists()
        if not is_relied_on:
            detach_from_family(member, family_row.family)
