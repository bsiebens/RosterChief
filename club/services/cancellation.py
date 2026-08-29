"""Cancelling one person's sign-up (management.views.SignupCancelView, the
public registration status page's own RegistrationCancelView).

Two different things can happen, depending on whether this club has ever
seen this member before this season:

- A returning member (has at least one other ClubMembership at this club,
  any season) gets a soft cancel -- ClubMembership.status flips to
  CANCELLED, everything else (fee history, checklist, any roster
  placement) stays exactly as it was, for the record.
- Someone who only exists because of this one sign-up has nothing worth
  keeping once it's withdrawn -- their Member row is deleted outright
  (cascading the ClubMembership itself, its RegistrationDetails,
  MemberRequirementStatus, FeePayment, and any roster/staff placement),
  rather than leaving a permanently-empty, cancelled shell in the member
  database. See registration.models.RegistrationDetails' own docstring on
  why registering is always "create/find the Member, then attach a
  ClubMembership" -- this is the reverse of that for the one-shot case.
"""

from club.models import ClubMembership


def is_new_member(member, club) -> bool:
    """Whether ``member`` has never had a ClubMembership at ``club`` before
    -- the "born this season, for this one sign-up" case cancel_membership
    cleans up entirely rather than leaving behind an empty record."""
    return not ClubMembership.objects.filter(club=club, member=member).exists()


def cancel_membership(membership):
    """Cancel ``membership``. Deletes the Member outright if this was their
    only ClubMembership at this club; otherwise just marks this season's row
    CANCELLED. Returns True if the Member was deleted, False if only
    cancelled."""
    club, member = membership.club, membership.member
    if ClubMembership.objects.filter(club=club, member=member).exclude(pk=membership.pk).exists():
        membership.status = ClubMembership.StatusChoices.CANCELLED
        membership.save(update_fields=["status"])
        return False

    member.delete()
    return True
