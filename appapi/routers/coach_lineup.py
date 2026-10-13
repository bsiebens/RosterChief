"""Coach line-up endpoints (API.md §7.3): ``/teams/{team_id}/events/{event_id}/lineup``.

The rules are events.services.lineup's, shared with the web coach screen. Reading is open to any
staff of the team and never creates anything; changing and publishing need a management
position (``can_publish_lineup``).
"""

import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Router

from club.services.access import current_season
from events.models import Attendance, Event, Lineup, LineupSelection
from events.services.attendance import player_attendance_rankings
from events.services.coach import LINEUP_EVENT_KINDS
from events.services.lineup import UNAVAILABLE_STATUSES, cancel_scheduled_publish, changes_on_publish, publish_lineup, schedule_lineup_publish
from events.services.rsvp import has_started
from teams.models import TeamMembership

from ..coach import CoachScope, coach_scope, photo_url_for
from ..coach_schemas import LineupIn, LineupOut, LineupPublishIn, LineupPublishOut
from ..errors import ApiError
from ..idempotency import idempotent
from ..presenters import event_out
from ..schemas import ErrorOut

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _game(coach: CoachScope, event_id) -> Event:
    """A game or tournament of the team; any other kind has no line-up (404)."""
    return get_object_or_404(Event.objects.select_related("location", "opponent").prefetch_related("teams"), pk=event_id, club=coach.club, teams=coach.team, kind__in=LINEUP_EVENT_KINDS)


def _status(lineup, has_selections: bool) -> str:
    if lineup is None:
        return "none"
    if lineup.published_at is not None:
        return "published"
    if lineup.scheduled_publish_at is not None:
        return "scheduled"
    return "draft" if has_selections else "none"


def lineup_out(request, coach: CoachScope, event: Event) -> dict:
    lineup = Lineup.objects.filter(event=event).first()
    selected = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True)) if lineup else set()
    season = current_season(coach.club)
    memberships, rates = {}, {}
    if season is not None:
        memberships = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=coach.team, season=season).select_related("position")}
        rates = {row["member"].pk: row["rate"] for row in player_attendance_rankings(coach.team, season, minimum_responses=1)}
    started = has_started(event)
    published = bool(lineup and lineup.published_at)

    members = []
    for attendance in Attendance.objects.filter(event=event).select_related("member"):
        member, membership = attendance.member, memberships.get(attendance.member_id)
        position = membership.position if membership and membership.position_id else None
        available = attendance.status not in UNAVAILABLE_STATUSES
        members.append(
            {
                "member_id": member.pk,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "jersey_number": membership.jersey_number if membership else None,
                "position": position.name if position else "",
                "position_order": position.ordering if position else 9999,
                "rsvp_status": attendance.status,
                "note": attendance.note,
                "available": available,
                "selected": member.pk in selected,
                "can_drop_out": published and attendance.status == Attendance.AttendanceStatus.SELECTED and not started,
                "attendance_rate": rates.get(member.pk),
                "photo_url": photo_url_for(request, member, coach.club),
            }
        )
    members.sort(key=lambda row: (row["position_order"], row["last_name"], row["first_name"]))
    newly_selected, newly_dropped = changes_on_publish(lineup) if lineup else (set(), set())
    return {
        "event": event_out(event),
        "status": _status(lineup, bool(selected)),
        "published_at": lineup.published_at if lineup else None,
        "scheduled_publish_at": lineup.scheduled_publish_at if lineup else None,
        "selected_count": len(selected),
        "pending": {"selected_member_ids": sorted(newly_selected, key=str), "dropped_member_ids": sorted(newly_dropped, key=str)},
        "members": members,
    }


@router.get("/teams/{team_id}/events/{event_id}/lineup", response={200: LineupOut, **ERRORS}, summary="A game's line-up")
def get_lineup(request, team_id: uuid.UUID, event_id: uuid.UUID):
    """Everybody invited with their answer, who is picked, and who a publish would notify. A pure
    read: asking for a line-up nobody has started creates nothing (`status: none`). Games and
    tournaments only (`404` for other kinds). Any staff of the team."""
    coach = coach_scope(request, team_id)
    return lineup_out(request, coach, _game(coach, event_id))


@router.put("/teams/{team_id}/events/{event_id}/lineup", response={200: LineupOut, **ERRORS, **INVALID}, summary="Save the picks")
def put_lineup(request, team_id: uuid.UUID, event_id: uuid.UUID, payload: LineupIn):
    """Replaces the set of selected players. Saving only stores the picks: nobody is told and
    nobody's answer changes until `…/lineup/publish`, also when the line-up is already published.
    Only available players (`present`, `maybe`, `selected`, `not_selected`) can be picked; an id that
    is not invited, or is `absent`, `excused` or `no_response`, fails the call with `422
    invalid_selection` and saves nothing. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _game(coach, event_id)
    wanted = set(payload.selected_member_ids)
    with transaction.atomic():
        available = set(Attendance.objects.filter(event=event).exclude(status__in=UNAVAILABLE_STATUSES).values_list("member_id", flat=True))
        invalid = wanted - available
        if invalid:
            raise ApiError(422, "invalid_selection", _("Some of these players cannot be selected."), fields={"selected_member_ids": sorted(map(str, invalid))})
        lineup, _created = Lineup.objects.get_or_create(event=event, defaults={"team": coach.team, "created_by": coach.club_scope.me})
        current = set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))
        LineupSelection.objects.filter(lineup=lineup, member_id__in=current - wanted).delete()
        LineupSelection.objects.bulk_create([LineupSelection(lineup=lineup, member_id=member_id) for member_id in wanted - current])
    return lineup_out(request, coach, event)


@router.post("/teams/{team_id}/events/{event_id}/lineup/publish", response={200: LineupPublishOut, **ERRORS, 409: ErrorOut, **INVALID}, summary="Publish, schedule or cancel a scheduled publish")
def publish(request, team_id: uuid.UUID, event_id: uuid.UUID, payload: LineupPublishIn):
    """`now` writes the picks into the players' answers (`selected` / `not_selected`) and tells the
    players whose situation changed since the last publish: newly selected ones "you're in", ones
    dropped since "you're not in it this time", nobody else. `notified` lists exactly who. The first
    publish of an empty line-up is `409 lineup_empty`. `schedule` (needs `at`, in the future) publishes
    by itself then; `cancel` drops the schedule. Send an `Idempotency-Key`. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _game(coach, event_id)

    def work():
        lineup = Lineup.objects.filter(event=event).first()
        notified = {"selected_member_ids": [], "dropped_member_ids": []}
        if payload.mode == "schedule":
            if payload.at is None or payload.at <= timezone.now():
                raise ApiError(422, "validation_error", fields={"at": [str(_("Pick a date and time in the future."))]})
            if lineup is None or not lineup.selections.exists():
                raise ApiError(409, "lineup_empty", _("Pick players before publishing."))
            schedule_lineup_publish(lineup, payload.at)
        elif payload.mode == "cancel":
            if lineup is not None:
                cancel_scheduled_publish(lineup)
        else:
            if lineup is None or (lineup.published_at is None and not lineup.selections.exists()):
                raise ApiError(409, "lineup_empty", _("Pick players before publishing."))
            with transaction.atomic():
                newly_selected, newly_dropped = changes_on_publish(lineup)
                publish_lineup(lineup)
            notified = {"selected_member_ids": sorted(newly_selected, key=str), "dropped_member_ids": sorted(newly_dropped, key=str)}
        return 200, {"lineup": lineup_out(request, coach, event), "notified": notified}

    return idempotent(request, work)
