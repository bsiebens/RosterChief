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
from formbuilder.models import FormSend, Submission
from members.models import GroupMembership, Member
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


def is_send_open(send, when=None):
    """Whether ``send`` is currently accepting submissions -- its own
    is_active/opens_at/closes_at window, independent of Form.is_active
    (which only governs whether the template can be picked for a *new*
    send, see Form's own docstring, and shouldn't retroactively hide a send
    already out)."""
    when = when or timezone.now()
    if not send.is_active:
        return False
    if send.opens_at is not None and when < send.opens_at:
        return False
    if send.closes_at is not None and when > send.closes_at:
        return False
    return True


def _effective_member_ids_by_send(sends):
    """``{send.pk: {member ids}}`` for every ``send`` in ``sends``, resolved in a
    handful of queries shared across the whole list -- what form_status_rows_for
    needs when it walks every FormSend the club has ever created, instead of
    effective_members()'s own ~4-6 queries repeated independently per send (a
    club with a season or two of history could turn that into 50-150+ queries
    on a single page load)."""
    sends = list(sends)
    if not sends:
        return {}

    season_by_send = {send.pk: resolve_season(send) for send in sends}
    club_wide_sends = [send for send in sends if send.club_wide]
    scoped_sends = [send for send in sends if not send.club_wide]

    club_wide_seasons = {season_by_send[send.pk] for send in club_wide_sends if season_by_send[send.pk] is not None}
    active_ids_by_season = {}
    if club_wide_seasons:
        for season_id, member_id in ClubMembership.objects.filter(club=sends[0].club, season__in=club_wide_seasons, status=ClubMembership.StatusChoices.ACTIVE).values_list("season_id", "member_id"):
            active_ids_by_season.setdefault(season_id, set()).add(member_id)

    team_ids_by_send = {send.pk: set(send.teams.values_list("id", flat=True)) for send in scoped_sends}
    group_ids_by_send = {send.pk: set(send.groups.values_list("id", flat=True)) for send in scoped_sends}
    all_team_ids = {team_id for ids in team_ids_by_send.values() for team_id in ids}
    all_group_ids = {group_id for ids in group_ids_by_send.values() for group_id in ids}
    scoped_seasons = {season_by_send[send.pk] for send in scoped_sends if season_by_send[send.pk] is not None}

    member_ids_by_team_season = {}
    if all_team_ids and scoped_seasons:
        for team_id, season_id, member_id in TeamMembership.objects.filter(team_id__in=all_team_ids, season_id__in=scoped_seasons).values_list("team_id", "season_id", "member_id"):
            member_ids_by_team_season.setdefault((team_id, season_id), set()).add(member_id)

    member_ids_by_group = {}
    if all_group_ids:
        for group_id, member_id in GroupMembership.objects.filter(group_id__in=all_group_ids).values_list("group_id", "member_id"):
            member_ids_by_group.setdefault(group_id, set()).add(member_id)

    invited_ids_by_send = {send.pk: set(send.invited_members.values_list("id", flat=True)) for send in sends}
    excluded_ids_by_send = {send.pk: set(send.excluded_members.values_list("id", flat=True)) for send in sends}

    result = {}
    for send in sends:
        season = season_by_send[send.pk]
        if send.club_wide:
            member_ids = set(active_ids_by_season.get(season.pk, set())) if season is not None else set()
        else:
            member_ids = set()
            if season is not None:
                for team_id in team_ids_by_send[send.pk]:
                    member_ids.update(member_ids_by_team_season.get((team_id, season.pk), set()))
            for group_id in group_ids_by_send[send.pk]:
                member_ids.update(member_ids_by_group.get(group_id, set()))
        member_ids.update(invited_ids_by_send[send.pk])
        member_ids.difference_update(excluded_ids_by_send[send.pk])
        result[send.pk] = member_ids
    return result


def form_status_rows_for(members, club):
    """Every FormSend any of ``members`` is or was ever addressed to, one
    row per (send, member) pair -- ``{"send": ..., "member": ...,
    "submission": ..., "submitted_at": ...}`` (the latter two ``None`` while
    still pending). Newest send first. The one shared building block behind
    mobile's Home "Forms to complete" card, Me's Forms counter, and the
    dedicated Forms list page (which also uses ``submission`` to link a
    completed row to that submission's own read-only "my responses" screen)
    -- each just filters/caps this differently rather than re-deriving it.

    ``members`` is a small, already-resolved list (the viewer's own
    person-scope, e.g. self + managed children); the audience for every one
    of the club's sends is resolved together via _effective_member_ids_by_send
    rather than independently per send."""
    members_by_id = {member.pk: member for member in members}
    if not members_by_id:
        return []

    submission_by_send_and_member = {(submission.send_id, submission.member_id): submission for submission in Submission.objects.filter(send__club=club, member_id__in=members_by_id)}

    sends = list(FormSend.objects.filter(club=club).select_related("form").order_by("-created"))
    audience_ids_by_send = _effective_member_ids_by_send(sends)

    rows = []
    for send in sends:
        for member_id in audience_ids_by_send[send.pk] & members_by_id.keys():
            submission = submission_by_send_and_member.get((send.pk, member_id))
            rows.append({"send": send, "member": members_by_id[member_id], "submission": submission, "submitted_at": submission.submitted_at if submission else None})
    return rows
