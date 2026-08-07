"""Keep an event's attendance rows in sync with its effective audience.

The audience of an event is the union of the current rosters of its ``teams``
(for the event's season) plus any individually ``invited_members``, minus any
``excluded_members``. Attendance rows are reconciled against that set, but only
for events that are still in the future — history is never rewritten.
"""

from django.db.models import Count, Q
from django.utils import timezone

from club.models import Season
from events.models import Attendance, Event
from members.models import Member
from teams.models import TeamMembership


def resolve_season(event):
    """The season whose rosters define the event's audience."""
    if event.season_id is not None:
        return event.season
    return Season.covering(event.club, event.start.date())


def effective_members(event):
    """Return the ``Member`` queryset invited to ``event``."""
    member_ids: set = set()

    season = resolve_season(event)
    if season is not None:
        team_ids = list(event.teams.values_list("id", flat=True))
        if team_ids:
            member_ids.update(TeamMembership.objects.filter(team_id__in=team_ids, season=season).values_list("member_id", flat=True))

    member_ids.update(event.invited_members.values_list("id", flat=True))
    member_ids.difference_update(event.excluded_members.values_list("id", flat=True))

    return Member.objects.filter(id__in=member_ids)


def sync_event_attendances(event):
    """Reconcile ``event``'s attendance rows with its effective audience.

    No-op for events that have already started. New members get a
    ``NO_RESPONSE`` row; members no longer invited have their row removed
    outright (hard reconcile).
    """
    if event.start < timezone.now():
        return

    desired_ids = set(effective_members(event).values_list("id", flat=True))
    existing_ids = set(event.attendances.values_list("member_id", flat=True))

    to_add = desired_ids - existing_ids
    if to_add:
        Attendance.objects.bulk_create([Attendance(event=event, member_id=member_id) for member_id in to_add])

    to_remove = existing_ids - desired_ids
    if to_remove:
        event.attendances.filter(member_id__in=to_remove).delete()


def record_check_in(attendance, *, showed_up):
    """Record whether ``attendance``'s member actually showed up, separate from
    their RSVP status -- the hook a future check-in UI (the coaches app) writes
    through. Nothing in this codebase calls this yet; it exists so a no-show can
    be recorded the moment something does."""
    attendance.showed_up = showed_up
    attendance.save(update_fields=["showed_up"])


def team_attendance_rate(team, season):
    """Turnout for ``team`` in ``season``: present / (present + absent) among
    past events -- same definition as
    controlpanel.services.statistics.attendance_rates, just scoped to one team
    instead of the whole club."""
    counts = Attendance.objects.filter(event__teams=team, event__season=season, event__start__lt=timezone.now()).aggregate(
        present=Count("id", filter=Q(status=Attendance.AttendanceStatus.PRESENT)),
        absent=Count("id", filter=Q(status=Attendance.AttendanceStatus.ABSENT)),
    )
    answered = counts["present"] + counts["absent"]
    return round(100 * counts["present"] / answered) if answered else None


def player_attendance_rankings(team, season, *, minimum_responses=3):
    """Each player's turnout this season, best first:
    ``[{"member": Member, "rate": int, "responses": int}, ...]``. Excludes
    anyone with fewer than ``minimum_responses`` present/absent replies -- one
    absence out of one invite would otherwise read as "0%, worst on the team."
    """
    rows = (
        Attendance.objects.filter(
            event__teams=team,
            event__season=season,
            event__start__lt=timezone.now(),
            status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT],
        )
        .values("member")
        .annotate(present=Count("id", filter=Q(status=Attendance.AttendanceStatus.PRESENT)), responses=Count("id"))
        .filter(responses__gte=minimum_responses)
    )

    members_by_id = Member.objects.in_bulk([row["member"] for row in rows])
    rankings = [{"member": members_by_id[row["member"]], "rate": round(100 * row["present"] / row["responses"]), "responses": row["responses"]} for row in rows]
    rankings.sort(key=lambda entry: entry["rate"], reverse=True)
    return rankings


def players_who_missed_recent_practices(team, season, *, count=2):
    """Members absent or silent (not excused) on *every one* of the team's
    ``count`` most recent past training-kind events this season. Empty if the
    team has fewer than ``count`` past practices logged yet -- not enough
    history to call anyone out."""
    practices = list(Event.objects.filter(teams=team, season=season, kind=Event.EventKind.TRAINING, start__lt=timezone.now()).order_by("-start")[:count])
    if len(practices) < count:
        return Member.objects.none()

    flagged = (Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.NO_RESPONSE)
    missed_by_member = None
    for practice in practices:
        missed_here = set(Attendance.objects.filter(event=practice, status__in=flagged).values_list("member_id", flat=True))
        missed_by_member = missed_here if missed_by_member is None else missed_by_member & missed_here

    return Member.objects.filter(pk__in=missed_by_member).order_by("last_name", "first_name")


def team_no_shows(team, season):
    """Attendance rows where the member RSVPed present/selected but was
    checked in as showed_up=False -- most recent first. Each entry carries
    both the member and the event: a no-show is about a specific missed
    occasion, not a season-long rate. Never inferred from a missing check-in --
    with nothing checking anyone in yet, that would flag every "present" RSVP."""
    return (
        Attendance.objects.filter(
            event__teams=team,
            event__season=season,
            status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED],
            showed_up=False,
        )
        .select_related("member", "event")
        .order_by("-event__start")
    )
