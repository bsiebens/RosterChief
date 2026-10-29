"""Saving a member's referee / official eligibility (their level, validity and exclusions).

Shared by the management member page (management.views.MemberRefereeEligibilityUpdateView and its official twin)
and api.panel.routers.members_onboarding. Both take the already validated management.forms
MemberRefereeEligibilityForm / MemberOfficialEligibilityForm.
"""


def save_referee_eligibility(form):
    """Save the referee profile and withdraw the open invites for games the person may no longer referee
    (accepted sign-ups stay). Returns the profile."""
    from events.services.referees import retract_ineligible_invites

    profile = form.save()
    retract_ineligible_invites(profile)
    return profile


def save_official_eligibility(form):
    """Save the official profile. Returns it."""
    return form.save()
