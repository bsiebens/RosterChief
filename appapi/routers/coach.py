"""Coach endpoints (API.md §5.2, §7.3), club host, ``/teams/{team_id}/…``.

Every rule comes from the services the member web app's coach mode uses
(events.services.{coach,attendance}); this module resolves the team scope and shapes JSON.
Reads are open to any staff of the team; the writes the web limits to a management position
need ``manager`` (``403 manager_required``) -- ``GET /context`` says which per team.
"""

import uuid
from typing import Annotated, Literal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from club.services.access import current_season, event_season
from events.models import Attendance, Event, Lineup
from events.services.attendance import CoachAnswerError, record_check_in, set_answer_as_coach
from events.services.coach import IN_STATUSES, LINEUP_EVENT_KINDS, OUT_STATUSES, remind_silent, still_current_events
from teams.models import StaffAssignment, TeamMembership

from ..coach import CoachScope, coach_scope, photo_url_for
from ..coach_schemas import AttendanceSheetOut, AttendanceStatusIn, CheckInsIn, RemindSilentOut, TodayOut
from ..errors import ApiError
from ..idempotency import idempotent
from ..presenters import event_out, team_ref
from ..schemas import ErrorOut

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
#: How many of the team's soonest upcoming games are checked for a missing line-up.
UPCOMING_GAMES_CHECKED = 5
EVENT_RELATED = ("location", "opponent")
FilterQuery = Annotated[Literal["responded", "silent", "declined", "all"], Query(description="Which rows: `responded` (default), `silent`, `declined` or `all`.")]


def _team_event(coach: CoachScope, event_id: uuid.UUID) -> Event:
    """An event of this club that the team is invited to, or 404."""
    return get_object_or_404(Event.objects.select_related(*EVENT_RELATED).prefetch_related("teams"), pk=event_id, club=coach.club, teams=coach.team)


@router.get("/teams/{team_id}/today", response={200: TodayOut, **ERRORS}, summary="The team's Today screen")
def today(request, team_id: uuid.UUID):
    """Squad size, the session to show (today's, else the next one) with in / out / silent
    counts and its reply deadline, and what needs the coach: silent players for that session
    and every upcoming game or tournament without a published line-up. Any staff of the team."""
    coach = coach_scope(request, team_id)
    now_date = timezone.localdate()
    season = current_season(coach.club)
    squad_count = TeamMembership.objects.filter(team=coach.team, season=season).count() if season else 0

    upcoming = still_current_events(coach.team).select_related(*EVENT_RELATED).prefetch_related("teams")
    tonight = upcoming.filter(start__date=now_date).first()
    session = tonight or upcoming.first()
    in_count = out_count = silent_count = 0
    needs_you = []
    if session is not None:
        rows = Attendance.objects.filter(event=session)
        in_count = rows.filter(status__in=IN_STATUSES).count()
        out_count = rows.filter(status__in=OUT_STATUSES).count()
        silent_count = rows.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).count()
        if silent_count:
            needs_you.append({"type": "silent_players", "event_id": session.pk, "title": session.title, "start": session.start, "count": silent_count})

    games = list(upcoming.filter(kind__in=LINEUP_EVENT_KINDS)[:UPCOMING_GAMES_CHECKED])
    published = set(Lineup.objects.filter(event__in=games, published_at__isnull=False).values_list("event_id", flat=True))
    for game in games:
        if game.pk not in published:
            needs_you.append({"type": "unpublished_lineup", "event_id": game.pk, "title": game.title, "start": game.start, "count": None})

    return {
        "team": team_ref(coach.team),
        "squad_count": squad_count,
        "event": event_out(session) if session else None,
        "is_today": tonight is not None,
        "closes_at": session.deadline if session else None,
        "in_count": in_count,
        "out_count": out_count,
        "silent_count": silent_count,
        "needs_you": needs_you,
    }


def _sheet(request, coach: CoachScope, event: Event, filter_name: str) -> dict:
    season = event_season(event)
    memberships = {tm.member_id: tm for tm in TeamMembership.objects.filter(team=coach.team, season=season).select_related("position")} if season else {}
    rows = list(Attendance.objects.filter(event=event).select_related("member").order_by("member__last_name", "member__first_name"))
    silent = Attendance.AttendanceStatus.NO_RESPONSE
    if filter_name == "silent":
        shown = [row for row in rows if row.status == silent]
    elif filter_name == "declined":
        shown = [row for row in rows if row.status in OUT_STATUSES]
    elif filter_name == "all":
        shown = rows
    else:
        shown = [row for row in rows if row.status in IN_STATUSES]

    items = []
    for row in shown:
        member = row.member
        membership = memberships.get(member.pk)
        items.append(
            {
                "attendance_id": row.pk,
                "member_id": member.pk,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "jersey_number": membership.jersey_number if membership else None,
                "position": membership.position.name if membership and membership.position_id else "",
                "status": row.status,
                "note": row.note,
                "showed_up": row.showed_up,
                "coach_note": row.coach_note if coach.is_manager else None,
                "answered_by_coach": row.answered_by_coach,
                "photo_url": photo_url_for(request, member, coach.club),
            }
        )
    return {
        "event": event_out(event),
        "filter": filter_name,
        "counts": {
            "total": len(rows),
            "responded": sum(1 for row in rows if row.status in IN_STATUSES),
            "silent": sum(1 for row in rows if row.status == silent),
            "declined": sum(1 for row in rows if row.status in OUT_STATUSES),
            "checked_in": sum(1 for row in rows if row.showed_up is not None),
        },
        "items": items,
    }


@router.get("/teams/{team_id}/events/{event_id}/attendance", response={200: AttendanceSheetOut, **ERRORS}, summary="Who is coming to an event")
def attendance(request, team_id: uuid.UUID, event_id: uuid.UUID, filter: FilterQuery = "responded"):
    """One row per invited person. `filter`: `responded` (the default: in or maybe),
    `silent`, `declined`, or `all`. `counts` always cover everybody. Any staff of the team."""
    coach = coach_scope(request, team_id)
    return _sheet(request, coach, _team_event(coach, event_id), filter)


@router.post("/teams/{team_id}/events/{event_id}/check-ins", response={200: AttendanceSheetOut, **ERRORS, 422: ErrorOut}, summary="Record who showed up")
def check_ins(request, team_id: uuid.UUID, event_id: uuid.UUID, payload: CheckInsIn, filter: FilterQuery = "responded"):
    """Sets `showed_up` for the listed rows, leaving the rest alone, and returns the sheet as
    `GET …/attendance` would (same `filter`). Repeating a call changes nothing. An id that is
    not a row of this event fails the whole call with `422 unknown_attendance`. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    wanted = {item.attendance_id: item.showed_up for item in payload.items}
    with transaction.atomic():
        rows = {row.pk: row for row in Attendance.objects.select_for_update().filter(event=event, pk__in=wanted)}
        unknown = [str(pk) for pk in wanted if pk not in rows]
        if unknown:
            raise ApiError(422, "unknown_attendance", _("Some of these rows do not belong to this event."), fields={"items": unknown})
        for pk, showed_up in wanted.items():
            record_check_in(rows[pk], showed_up=showed_up)
    return _sheet(request, coach, event, filter)


@router.post(
    "/teams/{team_id}/events/{event_id}/attendance/{attendance_id}/status",
    response={200: AttendanceSheetOut, **ERRORS, 409: ErrorOut, 422: ErrorOut},
    summary="Set a player's answer as a coach",
)
def set_attendance_status(request, team_id: uuid.UUID, event_id: uuid.UUID, attendance_id: uuid.UUID, payload: AttendanceStatusIn, filter: FilterQuery = "responded"):
    """Sets the player's In/Out answer and/or the private coach note, and returns the sheet as
    `GET …/attendance` would (same `filter`). Works after the reply deadline and after the
    event started; the row is marked `answered_by_coach`. `no_response` resets the answer.
    A player in the published line-up can only be marked `absent` (like their own drop-out:
    they become `absent`, the pick stays for you to swap, the event's other managers are told);
    `present` keeps them in, `maybe` / `no_response` are `422 invalid_status`. The player is
    told only with `notify: true` and never sees `coach_note`. Errors: `403 manager_required`,
    `404 not_found`, `409 event_cancelled`, `422 invalid_status`. Accepts an `Idempotency-Key`.
    Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    actor = StaffAssignment.objects.filter(member__user=request.user, team=coach.team, season=event_season(event)).select_related("member").first()

    def work():
        with transaction.atomic():
            attendance = get_object_or_404(Attendance.objects.select_for_update().select_related("event", "member"), pk=attendance_id, event=event)
            try:
                set_answer_as_coach(attendance, payload.status, payload.coach_note, coach=actor.member if actor else None, notify=payload.notify)
            except CoachAnswerError as exc:
                raise ApiError(422 if exc.code == "invalid_status" else 409, exc.code, exc.message) from exc
        return 200, _sheet(request, coach, event, filter)

    return idempotent(request, work)[1]


@router.post("/teams/{team_id}/events/{event_id}/remind-silent", response={200: RemindSilentOut, **ERRORS}, summary="Nudge players who have not answered")
def remind(request, team_id: uuid.UUID, event_id: uuid.UUID):
    """Sends the in-app (and, if they have the app, push) reminder to everyone still on
    `no_response`. Accepts an `Idempotency-Key`: a retry does not remind twice. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    event = _team_event(coach, event_id)
    return idempotent(request, lambda: (200, {"notified": remind_silent(event)}))[1]
