"""Member endpoints (API.md §7.2), club host: home, calendar, events, RSVP, referee and
official sign-ups, event tasks. Every rule comes from the services the member web app
uses (mobile.services.home, events.services.{agenda,member_view,rsvp,tasks,referees,
officials}); this module only resolves scope and shapes JSON."""

import datetime
import uuid
from typing import Annotated

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from events.models import Attendance, Event, EventTask, GameTimeline, OfficialSignup, RefereeSignup
from events.services.agenda import KIND_FILTERS, member_agenda
from events.services.game_timeline import timeline_for_api
from events.services.member_view import event_tasks, member_event_view
from events.services.officials import OfficialAssignmentError, accept_official_signup, decline_official_signup
from events.services.referees import RefereeAssignmentError, accept_referee_signup, decline_referee_signup
from events.services.rsvp import RsvpError, submit_rsvp
from events.services.tasks import TaskClaimError, claim_task, unclaim_task
from mobile.services.home import home_summary

from ..errors import ApiError
from ..presenters import attendance_out, event_out, money, news_summary, season_out, signup_out, task_out
from ..schemas import AttendanceOut, CalendarOut, ErrorOut, EventDetailOut, HomeOut, RsvpIn, SignupOut, SignupResponseIn, TaskOut
from ..scope import club_scope

router = Router(tags=["member"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
MEMBER_ID_HELP = "A managed person's id (see `/context`), or `all` (the default) for everyone the account acts for."
#: Longest calendar window one request may ask for.
MAX_CALENDAR_DAYS = 92
EVENT_RELATED = ("location", "opponent")


def _hero(attendance, *, closed=False) -> dict | None:
    if attendance is None:
        return None
    return {"event": event_out(attendance.event), "attendance": attendance_out(attendance), "rsvp_closed": closed}


@router.get("/home", response={200: HomeOut, **ERRORS}, summary="Home screen")
def home(request, member_id: Annotated[str | None, Query(description=MEMBER_ID_HELP)] = None):
    """Everything the Home screen shows, capped the way the web app caps it: the next
    unanswered invitation, the next event someone is going to, other open asks, free
    task slots, open dues, the latest news and forms still to fill in."""
    scope = club_scope(request)
    summary = home_summary(scope.club, scope.people_for(member_id), officials_enabled=scope.feature("officials"))

    needs_answer = []
    for row in summary["needs_answer"]:
        source = row.get("attendance") or row.get("signup")
        needs_answer.append({"type": row["kind"], "event": event_out(row["event"]), "member_id": row["member"].pk, "status": source.status, "signup_id": row["signup"].pk if "signup" in row else None})

    dues = []
    for row in summary["dues_rows"]:
        offer = row["early_payment"]
        membership = row["membership"]
        dues.append(
            {
                "member_id": membership.member_id,
                "membership_id": membership.pk,
                "season": season_out(membership.season),
                "balance": money(row["balance"]),
                "early_payment_deadline": offer["deadline"] if offer else None,
                "early_payment_total": money(offer["discounted_total"]) if offer else None,
            }
        )

    return {
        "rsvp_needed": _hero(summary["hero_attendance"], closed=summary["rsvp_closed"]),
        "next_going": _hero(summary["going_attendance"]),
        "needs_answer": needs_answer,
        "needs_answer_more": summary["needs_answer_total"] - len(summary["needs_answer"]),
        "open_tasks": [{"id": task.pk, "event_id": task.event_id, "event_title": task.event.title, "event_start": task.event.start, "title": task.title, "open_count": task.open_count} for task in summary["open_tasks"]],
        "open_tasks_more": summary["open_tasks_total"] - len(summary["open_tasks"]),
        "dues": dues,
        "news": [news_summary(request, item) for item in summary["news_items"]],
        "forms_to_complete": [{"send_id": row["send"].pk, "title": row["send"].form.title, "member_id": row["member"].pk, "closes_at": row["send"].closes_at} for row in summary["forms_to_complete"]],
    }


@router.get("/calendar", response={200: CalendarOut, **ERRORS, 422: ErrorOut}, summary="Agenda")
def calendar(
    request,
    from_: Annotated[datetime.datetime | None, Query(alias="from", description="Start of the window (default: now).")] = None,
    to: Annotated[datetime.datetime | None, Query(description=f"End of the window, exclusive (default and maximum: `from` + {MAX_CALENDAR_DAYS} days).")] = None,
    kind: Annotated[str | None, Query(description="`game` or `training`; anything else means all kinds.")] = None,
    member_id: Annotated[str | None, Query(description=MEMBER_ID_HELP)] = None,
):
    """Invitations, referee/official sign-ups (open or accepted) and events a person is
    kept off by an open club requirement (`blocked`), soonest first. Cancelled events
    are left out. Fetch the next window by passing the previous `to` as `from`."""
    scope = club_scope(request)
    start = from_ or timezone.now()
    limit = start + datetime.timedelta(days=MAX_CALENDAR_DAYS)
    end = to or limit
    if end <= start or end > limit:
        raise ApiError(422, "validation_error", fields={"to": [_("Must be after “from” and at most %(days)d days later.") % {"days": MAX_CALENDAR_DAYS}]})

    rows = member_agenda(scope.club, scope.people_for(member_id), kind=kind if kind in KIND_FILTERS else None, officials_enabled=scope.feature("officials"), start=start, end=end)
    items = []
    for row in rows:
        source = row.get("attendance") or row.get("signup")
        items.append(
            {
                "type": row["type"],
                "event": event_out(row["event"]),
                "member_id": row["member"].pk,
                "status": source.status if source else None,
                "signup_id": row["signup"].pk if "signup" in row else None,
                "blocked_requirements": [requirement.name for requirement in row.get("requirements", [])],
            }
        )
    return {"items": items}


def _club_event(scope, event_id) -> Event:
    return get_object_or_404(Event.objects.select_related(*EVENT_RELATED).prefetch_related("teams"), pk=event_id, club=scope.club)


@router.get("/events/{event_id}", response={200: EventDetailOut, **ERRORS}, summary="One event")
def event_detail(request, event_id: uuid.UUID):
    """The event as the account's family sees it: their answers, a published line-up,
    sign-ups, tasks and the squad's response counts."""
    scope = club_scope(request)
    event = _club_event(scope, event_id)
    view = member_event_view(event, scope.club, scope.people, scope.me, officials_enabled=scope.feature("officials"))
    lineup = view["lineup"]

    answers = []
    for row in view["your_answers"]:
        attendance, membership = row["attendance"], row["membership"]
        answers.append(
            {
                **attendance_out(attendance),
                "can_drop_out": lineup is not None and attendance.status == Attendance.AttendanceStatus.SELECTED and not view["started"],
                "team": {"team_id": membership.team_id, "position": membership.position.name if membership.position_id else None, "jersey_number": membership.jersey_number} if membership else None,
            }
        )

    return {
        "event": event_out(event),
        "rsvp_closed": view["rsvp_closed"],
        "started": view["started"],
        "lineup": {
            "published_at": lineup.published_at,
            "categories": [{"label": str(category["label"]), "members": [{"id": member.pk, "first_name": member.first_name, "last_name": member.last_name} for member in category["members"]]} for category in view["lineup_categories"]],
        }
        if lineup
        else None,
        "answers": answers,
        "blocked": [{"member_id": row["member"].pk, "requirements": [requirement.name for requirement in row["requirements"]]} for row in view["blocked_signups"]],
        "referee_signups": [signup_out(signup) for signup in view["referee_signups"]],
        "official_signups": [signup_out(signup) for signup in view["official_signups"]],
        "tasks": [task_out(task) for task in view["tasks"]],
        "squad_summary": {key: view["squad_summary"][key] for key in ("total", "responded", "in_count", "out_count", "no_reply_count")} if view["squad_summary"] else None,
        "game_timeline": _game_timeline(scope, event),
    }


def _game_timeline(scope, event):
    """The game's timeline for a logged-in member -- always with player names
    (the public API's Team.show_player_names_publicly gate is about anonymous
    visitors). None for anything but a game, or without the club's feature."""
    if event.kind != Event.EventKind.GAME or not scope.feature("game_timeline"):
        return None
    timeline = GameTimeline.objects.filter(event=event).defer("raw").first()
    return timeline_for_api(timeline, event, show_names=True) if timeline else None


#: RsvpError code -> HTTP status. Refusals of a well-formed answer are state conflicts.
RSVP_STATUS = {"invalid_status": 422, "note_required": 422}


@router.post("/events/{event_id}/rsvp", response={200: AttendanceOut, **ERRORS, 409: ErrorOut, 422: ErrorOut}, summary="Answer an invitation")
def rsvp(request, event_id: uuid.UUID, payload: RsvpIn):
    """Answer for one managed person. Safe to repeat: it sets the answer, and returns
    the stored state. Refusals: `409 event_started`, `409 rsvp_closed` (deadline
    passed), `409 not_in_lineup` (`dropout` without being selected), `422
    note_required` (`absent`/`dropout` without a reason). `404` when the person isn't
    invited."""
    scope = club_scope(request)
    person = scope.person(payload.member_id)
    event = _club_event(scope, event_id)
    attendance = get_object_or_404(Attendance.objects.select_related("event", "member"), event=event, member=person)
    try:
        submit_rsvp(attendance, payload.status, payload.note)
    except RsvpError as exc:
        fields = {"note": [str(exc.message)]} if exc.code == "note_required" else None
        raise ApiError(RSVP_STATUS.get(exc.code, 409), exc.code, exc.message, fields=fields) from exc
    return attendance_out(attendance)


def _respond(scope, signup, response: str, accept, decline, error_class) -> dict:
    if response == "accept":
        try:
            accept(signup)
        except error_class as exc:
            raise ApiError(409, "signup_unavailable", str(exc)) from exc
    else:
        decline(signup)
    signup.refresh_from_db()
    return signup_out(signup)


@router.post("/events/{event_id}/referee-signups/{signup_id}/respond", response={200: SignupOut, **ERRORS, 409: ErrorOut}, summary="Accept or decline refereeing")
def respond_referee(request, event_id: uuid.UUID, signup_id: uuid.UUID, payload: SignupResponseIn):
    """For a managed person's referee invite. `409 signup_unavailable` when it can't be
    accepted (e.g. every referee slot is taken); the invite stays open."""
    scope = club_scope(request)
    signup = get_object_or_404(RefereeSignup.objects.select_related("event", "member"), pk=signup_id, event_id=event_id, event__club=scope.club, member__in=scope.people)
    return _respond(scope, signup, payload.response, accept_referee_signup, decline_referee_signup, RefereeAssignmentError)


@router.post("/events/{event_id}/official-signups/{signup_id}/respond", response={200: SignupOut, **ERRORS, 409: ErrorOut}, summary="Accept or decline being an official")
def respond_official(request, event_id: uuid.UUID, signup_id: uuid.UUID, payload: SignupResponseIn):
    """As for referees; needs the club's `officials` feature (`404 feature_disabled`)."""
    scope = club_scope(request)
    scope.require_feature("officials")
    signup = get_object_or_404(OfficialSignup.objects.select_related("event", "member"), pk=signup_id, event_id=event_id, event__club=scope.club, member__in=scope.people)
    return _respond(scope, signup, payload.response, accept_official_signup, decline_official_signup, OfficialAssignmentError)


def _task_state(scope, task) -> dict:
    fresh = next(item for item in event_tasks(task.event, scope.me) if item.pk == task.pk)
    return task_out(fresh)


def _club_task(scope, event_id, task_id) -> EventTask:
    if scope.me is None:
        raise ApiError(403, "forbidden")
    return get_object_or_404(EventTask.objects.select_related("event", "event__club"), pk=task_id, event_id=event_id, event__club=scope.club)


@router.post("/events/{event_id}/tasks/{task_id}/claim", response={200: TaskOut, **ERRORS, 409: ErrorOut}, summary="Take a task slot")
def claim(request, event_id: uuid.UUID, task_id: uuid.UUID):
    """Claimed as the account's own person. `409 task_unavailable` when it's full or
    already claimed by this account."""
    scope = club_scope(request)
    task = _club_task(scope, event_id, task_id)
    try:
        claim_task(task, scope.me)
    except TaskClaimError as exc:
        raise ApiError(409, "task_unavailable", str(exc)) from exc
    return _task_state(scope, task)


@router.delete("/events/{event_id}/tasks/{task_id}/claim", response={200: TaskOut, **ERRORS}, summary="Give a task slot back")
def unclaim(request, event_id: uuid.UUID, task_id: uuid.UUID):
    """Safe to repeat: without a claim it just returns the task."""
    scope = club_scope(request)
    task = _club_task(scope, event_id, task_id)
    existing = task.claims.filter(member=scope.me).first()
    if existing is not None:
        unclaim_task(existing)
    return _task_state(scope, task)
