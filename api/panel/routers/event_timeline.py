"""Management game day (``/manage/events/{id}/...``): the game timeline, check-ins and the silent-player reminder.

* ``GET …/timeline`` is the JSON counterpart of the event page's "Game timeline" card: what the
  competition's timeline says happened (``events.services.game_timeline.timeline_for_api``, the
  shape the member app serves, always with names) plus the detected moments. Same visibility as the
  event page: any staff, narrowed by ``scoped_to_managed_teams`` (an event outside it is a 404).
  The timeline is fetched from the competition, never typed in, so there is nothing to edit here;
  the fetch itself belongs to the event's "fetch game info" action.
* ``POST …/check-ins`` and ``POST …/remind-silent`` are the coach API's actions
  (``events.services.attendance.record_check_ins``, ``events.services.coach.remind_silent``) for
  someone who may manage the event: ``EventManagerRequiredMixin``'s rule, a club admin, a manager
  of one of its teams or a member of one of its groups (``403 forbidden``; 404 for another club's).
"""

import uuid

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.events_access import _get_event, _get_manageable_event
from api.panel.guards import panel_club
from api.panel.schemas.event_timeline import CheckInSheetOut, CheckInsIn, GameTimelineOut, RemindSilentOut
from api.protected.presenters import event_out
from events.models import Attendance, Event, GameTimeline
from events.services.attendance import UnknownAttendanceRows, record_check_ins
from events.services.coach import remind_silent
from events.services.game_moments import describe_moment
from events.services.game_timeline import timeline_for_api

router = Router(tags=["manage-event-game-day"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


# --- timeline -------------------------------------------------------------------------------


def _moment_out(moment) -> dict:
    status = "pushed" if moment.delivered_at else "pending" if moment.notifiable else "catch_up"
    return {"id": moment.pk, "kind": moment.kind, "text": describe_moment(moment), "game_seconds": moment.game_seconds, "detected_at": moment.created_at, "status": status}


@router.get("/manage/events/{uuid:event_id}/timeline", response={200: GameTimelineOut, **ERRORS}, summary="What happened in a game, and the moments detected from it")
def game_timeline(request, event_id: uuid.UUID):
    """The stored timeline (goals, penalties, goalie changes, time-outs, period scores, with player names),
    when it was fetched, the last fetch error, and the detected `moments`. `timeline` is null for an event
    that is not a game or was never fetched. Any staff who can see the event."""
    club = panel_club(request)
    event = _get_event(request, club, event_id)
    timeline = GameTimeline.objects.filter(event=event).defer("raw").first() if event.kind == Event.EventKind.GAME else None
    if timeline is None:
        return {"event_id": event.pk, "source": None, "timeline": None, "fetched_at": None, "changed_at": None, "finalized_at": None, "last_error": "", "moments": []}
    return {
        "event_id": event.pk,
        "source": timeline.source,
        "timeline": timeline_for_api(timeline, event, show_names=True),
        "fetched_at": timeline.fetched_at,
        "changed_at": timeline.changed_at,
        "finalized_at": timeline.finalized_at,
        "last_error": timeline.last_error,
        "moments": [_moment_out(moment) for moment in event.moments.all()],
    }


# --- check-ins and reminders ----------------------------------------------------------------


def _sheet(event) -> dict:
    rows = list(Attendance.objects.filter(event=event).select_related("member").order_by("member__last_name", "member__first_name"))
    counts = dict.fromkeys(Attendance.AttendanceStatus.values, 0)
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    counts["total"] = len(rows)
    counts["checked_in"] = sum(1 for row in rows if row.showed_up is not None)
    items = [
        {
            "attendance_id": row.pk,
            "member_id": row.member_id,
            "first_name": row.member.first_name,
            "last_name": row.member.last_name,
            "status": row.status,
            "note": row.note,
            "showed_up": row.showed_up,
            "answered_by_coach": row.answered_by_coach,
            "coach_note": row.coach_note,
        }
        for row in rows
    ]
    return {"event": event_out(event), "counts": counts, "items": items}


@router.post("/manage/events/{uuid:event_id}/check-ins", response={200: CheckInSheetOut, **ERRORS, 422: ErrorOut}, summary="Record who showed up")
def check_ins(request, event_id: uuid.UUID, payload: CheckInsIn):
    """Sets `showed_up` for the listed rows, leaving the rest alone, and returns the whole sheet. Repeating a call
    changes nothing. An id that is not a row of this event fails the whole call with `422 unknown_attendance`.
    Admins, managers of one of the event's teams, members of one of its groups."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    wanted = {item.attendance_id: item.showed_up for item in payload.items}
    try:
        record_check_ins(event, wanted)
    except UnknownAttendanceRows as exc:
        raise ApiError(422, "unknown_attendance", _("Some of these rows do not belong to this event."), fields={"items": exc.ids}) from exc
    return _sheet(event)


@router.post("/manage/events/{uuid:event_id}/remind-silent", response={200: RemindSilentOut, **ERRORS}, summary="Nudge people who have not answered")
def remind(request, event_id: uuid.UUID):
    """Sends the in-app (and push) reminder to everyone still on `no_response`; the same text as the daily
    deadline reminder. Accepts an `Idempotency-Key`: a retry does not remind twice. Same callers as check-ins."""
    club = panel_club(request)
    event = _get_manageable_event(request, club, event_id)
    return idempotent(request, lambda: (200, {"notified": remind_silent(event)}))[1]
