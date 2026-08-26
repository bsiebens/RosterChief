"""Resolve who a FormSend's audience actually is.

Same union/subtract shape as events.services.attendance.effective_members --
team rosters (for the send's season) union group members, or every active
ClubMembership for a club_wide send, then union invited_members and subtract
excluded_members. Deliberately NOT sharing code with events: there's no
onboarding-requirement subtraction here (that's keyed on Event.kind, which
forms have no equivalent of) and no Attendance-like row to sync -- this is a
pure query helper, not a persisted reconciliation.
"""

from django.utils import timezone

from club.models import ClubMembership
from club.services.access import current_season
from formbuilder.models import FormSend
from members.models import Member
from teams.models import TeamMembership


def resolve_season(send):
    """The season whose rosters define the send's audience."""
    if send.season_id is not None:
        return send.season
    return current_season(send.club)


def effective_members(send):
    """Return the ``Member`` queryset this send is addressed to."""
    season = resolve_season(send)

    member_ids = set()
    if send.club_wide:
        if season is not None:
            member_ids.update(ClubMembership.objects.filter(club=send.club, season=season, status=ClubMembership.StatusChoices.ACTIVE).values_list("member_id", flat=True))
    else:
        if season is not None:
            team_ids = list(send.teams.values_list("id", flat=True))
            if team_ids:
                member_ids.update(TeamMembership.objects.filter(team_id__in=team_ids, season=season).values_list("member_id", flat=True))

        group_ids = list(send.groups.values_list("id", flat=True))
        if group_ids:
            member_ids.update(Member.objects.filter(group_memberships__group_id__in=group_ids).values_list("id", flat=True))

    member_ids.update(send.invited_members.values_list("id", flat=True))
    member_ids.difference_update(send.excluded_members.values_list("id", flat=True))

    return Member.objects.filter(id__in=member_ids)


def members_not_yet_submitted(send):
    """``effective_members(send)`` minus anyone with an existing Submission --
    "who still needs to do this", the shared query behind the mobile home
    card, the Me page, and the reminder job."""
    submitted_ids = send.submissions.values_list("member_id", flat=True)
    return effective_members(send).exclude(id__in=submitted_ids)


def pending_sends_for(members, club):
    """Every currently-open FormSend in ``club`` whose still-pending audience
    intersects ``members`` -- what mobile.views.HomeView's "Forms to
    complete" card shows. Gated on the send's own is_active/opens_at/
    closes_at window only -- not on Form.is_active, which only governs
    whether the template can be picked for a *new* send (see Form's own
    docstring), and shouldn't retroactively hide a send already out.
    ``members`` is a small, already-resolved list (the viewer's own
    person-scope, e.g. self + managed children), so this checks each open
    send in turn rather than trying to express the intersection as one query
    -- the number of open sends for a club at any moment is small, matching
    the same non-vectorised cost profile events.services.attendance.
    effective_members itself already accepts."""
    member_ids = {member.pk for member in members}
    if not member_ids:
        return []

    now = timezone.now()
    open_sends = FormSend.objects.filter(club=club, is_active=True).exclude(opens_at__gt=now).exclude(closes_at__lt=now).select_related("form")

    pending = []
    for send in open_sends:
        not_yet = set(members_not_yet_submitted(send).values_list("id", flat=True))
        if member_ids & not_yet:
            pending.append(send)
    return pending
