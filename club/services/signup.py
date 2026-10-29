"""The Sign-up queue (management "Sign-up" screen and its API twin): who still needs processing this season,
and rostering one of them onto a team.

Shared by management.views.SignupDashboardView / SignupPlaceInTeamView and api.panel.routers.members_onboarding.
"""

from django.db.models import Q

from club.models import ClubMembership
from club.services.onboarding import annotate_onboarding_status, blocking_event_kinds, checklist_for, is_signup_clean
from club.services.signup_linking import possible_duplicates

#: Lapsed/cancelled memberships have nothing left for the Sign-up queue to do.
SIGNUP_QUEUE_STATUSES = (ClubMembership.StatusChoices.PENDING, ClubMembership.StatusChoices.ACTIVE)


def signup_queue(club, season) -> list:
    """Every current-season MEMBER-kind membership with something left to do, PENDING first and oldest first, each
    carrying the attributes the screen shows: ``onboarding_open``, ``checklist`` (requirement, status-or-None pairs),
    ``blocked_kinds``, ``teams_registered``, ``requested_team_ids``, ``possible_duplicates``, ``is_clean`` and
    ``is_oldest_pending``. A registration-originated membership appears only once its own invoice has been confirmed
    (registration.services.invoicing.membership_ids_awaiting_confirmation); an ACTIVE and clean one is dropped."""
    from registration.services.invoicing import membership_ids_awaiting_confirmation
    from teams.models import Team

    if season is None:
        return []
    teams = Team.objects.filter(club=club)
    # registration_details is a plain FK (one membership can carry more than one request), so prefetch_related.
    memberships = list(
        ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER, status__in=SIGNUP_QUEUE_STATUSES)
        .exclude(pk__in=membership_ids_awaiting_confirmation(club))
        .select_related("member")
        .prefetch_related("registration_details__requested_team")
    )
    # Sorted in Python, not via .order_by("-status", ...): StatusChoices' string values only put PENDING first
    # alphabetically by accident, and that would silently break the moment a choice's value changes.
    memberships.sort(key=lambda membership: (membership.status != ClubMembership.StatusChoices.PENDING, membership.created))
    annotate_onboarding_status(memberships)
    oldest_pending_id = next((membership.pk for membership in memberships if membership.status == ClubMembership.StatusChoices.PENDING), None)
    for membership in memberships:
        membership.checklist = checklist_for(membership)
        membership.blocked_kinds = blocking_event_kinds(membership)
        # Either kind of placement counts as "already placed": a roster spot (TeamMembership, players) or a staff spot
        # (StaffAssignment, volunteers auto-placed at confirm time by RegistrationInvoiceReviewView.place_confirmed_entries).
        membership.teams_registered = teams.filter(Q(roster__member_id=membership.member_id, roster__season=season) | Q(staff_assignments__member_id=membership.member_id, staff_assignments__season=season)).distinct()
        membership.requested_team_ids = {details.requested_team_id for details in membership.registration_details.all() if details.requested_team_id}
        # A fresh sign-up is never matched to an existing member automatically (registration.services.submission);
        # this is where staff sees the likely match.
        membership.possible_duplicates = list(possible_duplicates(membership.member, club))
        membership.is_clean = is_signup_clean(membership)
        membership.is_oldest_pending = membership.pk == oldest_pending_id
    # Already-ACTIVE *and* clean (paid, checklist fully resolved) memberships have nothing left for this queue to do.
    return [membership for membership in memberships if not (membership.status == ClubMembership.StatusChoices.ACTIVE and membership.is_clean)]


def place_signup_on_team(membership, team, season):
    """Rosters ``membership``'s member on ``team`` for ``season`` from the Sign-up page, honouring the jersey number
    requested (and pool-checked) at registration, and stamping the registration request as fulfilled so cancelling the
    membership later knows this placement came from a registration (club.services.cancellation.cancel_membership).
    Returns the TeamMembership. The caller has already checked the member isn't on the team."""
    from registration.models import RegistrationDetails
    from teams.services.roster import place_member_on_team

    requested_number = RegistrationDetails.objects.filter(membership=membership, requested_team=team).exclude(requested_jersey_number=None).values_list("requested_jersey_number", flat=True).first()
    team_membership = place_member_on_team(membership.member, team, season, jersey_number=requested_number)
    RegistrationDetails.objects.filter(membership=membership, requested_team=team, resulting_team_membership__isnull=True).update(resulting_team_membership=team_membership)
    return team_membership
