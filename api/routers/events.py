"""Calendar API (``/api/v1/events``): events and series, attendance, line-ups, tasks and attachments.

Rights (``members.services.access``):

* **View**: everyone who got into the management app sees every event, its attendance (without coach notes), its
  published line-up, its tasks and attachments.
* **Schedule** (create / edit / delete / cancel an event or series): ``can_schedule`` for the audience. Settings holders
  always; others only for events entirely about teams they manage and/or groups they administer, never club-wide.
  ``GET /events/options`` only offers what that rule allows. Rows carry ``can_edit``.
* **Attendance** (answers, coach notes, check-ins, line-up, tasks, attachments): ``can_manage_attendance``: settings
  holders, a manager of one of the event's teams or an admin of one of its groups. Rows carry ``can_manage_attendance``. ``coach_note`` is only ever
  returned to these people.

403 for a refused write, 404 for unknown ids, 422 ``{detail, code, errors}`` for rule violations. Writes go through
``events.services.*``; invited people are told about new / cancelled / rescheduled events after the commit
(``events.services.event_notifications``). Static routes are declared before ``/{event_id}``."""

import datetime
import uuid
from collections import defaultdict
from typing import Annotated

from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from ninja import File, Query, Router, UploadedFile
from ninja.errors import HttpError

from api.schemas_events import (
    AnswerIn,
    AttachmentListOut,
    AttachmentOut,
    AttendanceChangeOut,
    AttendanceListOut,
    AttendanceRowOut,
    BulkAnswerIn,
    BulkAnswerOut,
    CheckInIn,
    ClaimIn,
    EventDetailOut,
    EventFormOut,
    EventIn,
    EventOptionsOut,
    EventPatch,
    EventRowOut,
    FollowedTeamsOut,
    FollowStateOut,
    GameMomentOut,
    GameSheetOut,
    LineupIn,
    LineupOut,
    LineupPublishOut,
    LineupScheduleIn,
    RemindOut,
    TaskIn,
    TaskListOut,
    TaskOut,
    TaskPatch,
)
from events.models import Attendance, AttendanceChange, Event, EventAttachment, EventSubtype, EventTask, GameTimeline, Lineup, LineupSelection, Location, Opponent
from events.services import attachments as attachment_svc
from events.services import attendance as attendance_svc
from events.services import calendar as calendar_svc
from events.services import event_notifications as notices
from events.services import events as svc
from events.services import lineup as lineup_svc
from events.services import recurrence as rec
from events.services import tasks as task_svc
from members.models import Group, Member
from members.services import access
from members.services.errors import Invalid, NotAllowed
from shop.services.invoices import PDFUnavailable
from teams.models import Team, TeamMembership

router = Router()

MAX_RANGE_DAYS = 400
SCOPE_HELP = "this | following | all (occurrences of a series; ignored for one-off events)"
STATUS_KEYS = ("present", "absent", "excused", "maybe", "no_response", "selected", "not_selected")


# -- rights ------------------------------------------------------------------------------------------------------------


class Rights:
    """The caller's scheduling / attendance rights, loaded once for a whole page of events (the same rules as
    ``access.can_manage_event`` and ``access.can_manage_attendance``, without a query per event)."""

    def __init__(self, user):
        self.settings = access.can_manage_settings(user)
        self.active = bool(user is not None and user.is_authenticated and user.is_active)
        self.managed = set() if self.settings else access.managed_team_ids(user)
        self.groups = set() if self.settings else access.admin_group_ids(user)

    def can_edit(self, team_ids: set, group_ids: set, club_wide: bool) -> bool:
        if self.settings:
            return True
        if club_wide or not (team_ids or group_ids) or not self.active:
            return False
        return team_ids <= self.managed and group_ids <= self.groups

    def can_attend(self, team_ids: set, group_ids: set = frozenset()) -> bool:
        return self.settings or bool(team_ids & self.managed) or bool(group_ids & self.groups)


def _event(event_id) -> Event:
    event = Event.objects.select_related("location", "opponent", "subtype", "series", "season", "created_by").filter(pk=event_id).first()
    if event is None:
        raise HttpError(404, "Not found.")
    return event


def _require_attendance(request, event) -> None:
    if not access.can_manage_attendance(request.user, event):
        raise NotAllowed("You are not allowed to manage the attendance of this event.")


def _member(member_id) -> Member:
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise HttpError(404, "Not found.")
    return member


# -- output ------------------------------------------------------------------------------------------------------------


def _ref(obj, name=None) -> dict:
    out = {"id": obj.pk, "name": name if name is not None else (obj.get_full_name() if isinstance(obj, Member) else obj.name)}
    if isinstance(obj, Team):
        out["short_name"] = obj.short_name
    return out


def _location(loc) -> dict | None:
    return {"id": loc.pk, "name": loc.name, "city": loc.city, "address": loc.address, "is_home": loc.is_home} if loc is not None else None


def _opponent(o) -> dict | None:
    return {"id": o.pk, "name": o.name, "logo_url": o.logo.url if o.logo else None} if o is not None else None


def _empty_counts() -> dict:
    return dict.fromkeys((*STATUS_KEYS, "total"), 0)


def _counts_for(event_ids) -> dict:
    """``{event_id: counts}`` for every event in one grouped query."""
    out = defaultdict(_empty_counts)
    for row in Attendance.objects.filter(event_id__in=list(event_ids)).values("event_id", "status").annotate(n=Count("id")).order_by():
        counts = out[row["event_id"]]
        if row["status"] in counts:
            counts[row["status"]] += row["n"]
        counts["total"] += row["n"]
    return out


def _row(event, rights: Rights, counts: dict) -> dict:
    teams, groups = list(event.teams.all()), list(event.groups.all())
    team_ids, group_ids = {t.pk for t in teams}, {g.pk for g in groups}
    return {
        "id": event.pk,
        "title": event.title,
        "kind": event.kind,
        "subtype_id": event.subtype_id,
        "subtype_name": event.subtype.name if event.subtype_id else "",
        "start": event.start,
        "end": event.end,
        "gathering": event.gathering,
        "deadline": event.deadline,
        "cancelled": event.cancelled,
        "location": _location(event.location),
        "opponent": _opponent(event.opponent),
        "teams": [_ref(t) for t in teams],
        "groups": [_ref(g) for g in groups],
        "club_wide": event.club_wide,
        "series_id": event.series_id,
        "detached": event.detached,
        "is_friendly": event.is_friendly,
        "score_for": event.score_for,
        "score_against": event.score_against,
        "is_home_game": event.is_home_game,
        "has_game_source": bool(event.competition and event.external_game_id),
        "attendance": counts,
        "can_edit": rights.can_edit(team_ids, group_ids, event.club_wide),
        "can_manage_attendance": rights.can_attend(team_ids, group_ids),
    }


def _season_ref(season) -> dict | None:
    return {"id": season.pk, "name": season.name} if season is not None else None


def _series_info(series) -> dict | None:
    if series is None:
        return None
    return {
        "id": series.pk,
        "title": series.title,
        "rrule": series.rrule,
        "description": str(rec.describe_recurrence(series)),
        "dtstart": series.dtstart,
        "until": series.until,
        "recurrence": rec.recurrence_values(series),
    }


def _lineup_info(event) -> dict:
    lineup = Lineup.objects.filter(event=event).first()
    return {
        "available": lineup_svc.has_lineup(event),
        "status": lineup_svc.status(lineup),
        "team_id": lineup.team_id if lineup else None,
        "published_at": lineup.published_at if lineup else None,
        "scheduled_publish_at": lineup.scheduled_publish_at if lineup else None,
    }


def _officiating(rows) -> list[dict]:
    """The referees or officials on a game, by type then name (members link to their profile)."""
    from events.services.referees import order_assignments

    return [
        {"name": row.display_name, "member_id": row.member_id, "type_name": row.referee_type.name if row.referee_type_id else "", "is_external": row.member_id is None} for row in order_assignments(list(rows.select_related("member", "referee_type")))
    ]


def _detail(request, event) -> dict:
    event = Event.objects.select_related("location", "opponent", "subtype", "series", "season", "created_by").prefetch_related("teams", "groups", "invited_members", "excluded_members").get(pk=event.pk)
    out = _row(event, Rights(request.user), _counts_for([event.pk])[event.pk])
    invited, excluded = list(event.invited_members.all()), list(event.excluded_members.all())
    out.update(
        season=_season_ref(attendance_svc.resolve_season(event)),
        invited_member_ids=[m.pk for m in invited],
        excluded_member_ids=[m.pk for m in excluded],
        invited_members=[_ref(m) for m in invited],
        excluded_members=[_ref(m) for m in excluded],
        series=_series_info(event.series),
        lineup=_lineup_info(event),
        competition=event.competition,
        competition_label=event.competition_label,
        notes=event.notes,
        score_for=event.score_for,
        score_against=event.score_against,
        is_live=event.is_live,
        is_home_game=event.is_home_game,
        created_by=_ref(event.created_by) if event.created_by_id else None,
        task_count=event.tasks.count(),
        attachment_count=event.attachments.count(),
        referees=_officiating(event.referees),
        officials=_officiating(event.officials),
        has_game_sheet=GameTimeline.objects.filter(event=event).exclude(data={}).exists(),
    )
    return out


# -- calendar ----------------------------------------------------------------------------------------------------------


def _day_start(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


@router.get("", response=list[EventRowOut], summary="Events in a date range (everyone)", operation_id="list_events")
def list_events(
    request,
    from_: Annotated[datetime.date, Query(alias="from")],
    to: datetime.date,
    team: uuid.UUID | None = None,
    group: uuid.UUID | None = None,
    location: uuid.UUID | None = None,
    opponent: uuid.UUID | None = None,
    kind: str | None = None,
    mine: bool = False,
):
    """Events overlapping ``from`` .. ``to`` (dates, both inclusive, local time, at most 100 days), ordered by start;
    cancelled ones included (flagged). ``mine``: only events whose audience includes your own member record."""
    if to < from_:
        raise Invalid("The end of the range is before its start.", {"to": ["Must be on or after from."]})
    if (to - from_).days > MAX_RANGE_DAYS:
        raise Invalid(f"Ask for at most {MAX_RANGE_DAYS} days at a time.", {"to": [f"At most {MAX_RANGE_DAYS} days after from."]})
    if kind and kind not in Event.EventKind.values:
        raise Invalid("Unknown kind.", {"kind": ["Choose a valid kind."]})
    mine_member = None
    if mine:
        mine_member = Member.objects.filter(user=request.user).first()
        if mine_member is None:
            return []
    events = list(calendar_svc.events_between(_day_start(from_), _day_start(to + datetime.timedelta(days=1)), team_id=team, group_id=group, location_id=location, opponent_id=opponent, kind=kind or None, mine_member=mine_member))
    rights = Rights(request.user)
    counts = _counts_for([e.pk for e in events])
    return [_row(e, rights, counts[e.pk]) for e in events]


@router.get("/options", response=EventOptionsOut, summary="What the add / edit form may offer you", operation_id="event_options")
def event_options(request):
    """``teams``: all teams for settings holders, else the teams you manage. ``groups``: all groups for settings holders,
    else the groups you are in. ``can_club_wide``: settings holders only. Locations, opponents and kinds are complete."""
    rights = Rights(request.user)
    teams = Team.objects.all() if rights.settings else Team.objects.filter(pk__in=rights.managed)
    groups = Group.objects.all() if rights.settings else Group.objects.filter(pk__in=rights.groups)
    teams, groups = list(teams), list(groups.order_by("name"))
    return {
        "can_create": rights.settings or bool(teams) or bool(groups),
        "teams": [_ref(t) for t in teams],
        "groups": [_ref(g) for g in groups],
        "filter_teams": [_ref(t) for t in Team.objects.all()],
        "filter_groups": [_ref(g) for g in Group.objects.order_by("name")],
        "can_club_wide": rights.settings,
        "locations": [_location(loc) for loc in Location.objects.order_by("name")],
        "opponents": [_opponent(o) for o in Opponent.objects.order_by("name")],
        "kinds": [{"value": value, "label": str(label)} for value, label in Event.EventKind.choices],
        "subtypes": [{"id": s.pk, "kind": s.kind, "name": s.name, "title_template": s.title_template} for s in EventSubtype.objects.all()],
        "frequencies": list(rec.FREQUENCIES),
        "weekdays": [{"code": code, "label": str(rec.WEEKDAY_LABELS[code])} for code in rec.WEEKDAY_CODES],
    }


ONE_OFF_ONLY = ("season_id", "is_friendly", "cancelled")


def _refuse_one_off_fields(values: dict) -> None:
    bad = {key: ["Only for a single event, not for a series."] for key in ONE_OFF_ONLY if values.get(key)}
    if bad:
        raise Invalid(svc.MSG, bad)
    for key in ONE_OFF_ONLY:
        values.pop(key, None)


def _recurrence(values: dict) -> dict:
    """Pops ``recurrence`` / ``rrule`` from ``values`` and returns the series keys that were given."""
    out = {}
    recurrence, rrule = values.pop("recurrence", None), values.pop("rrule", None)
    if recurrence:
        out["recurrence"] = recurrence
    if rrule:
        out["rrule"] = rrule
    return out


@router.post("", response={201: EventDetailOut}, summary="Create an event or a recurring series", operation_id="create_event")
def create_event(request, payload: EventIn):
    """With ``recurrence`` (or a raw ``rrule``) a series is created and its first upcoming occurrence (else its first)
    is returned. Invited people with a login are told unless ``notify`` is false: one message per person, also for a
    series. 403 when the audience is not yours to schedule for (teams you do not manage, groups you are not in,
    club-wide without the settings permission)."""
    values = payload.dict()
    notify = values.pop("notify")
    series_keys = _recurrence(values)
    user = request.user
    with transaction.atomic():
        if series_keys:
            _refuse_one_off_fields(values)
            series = svc.create_series(user, **values, **series_keys)
            event = series.occurrences.filter(start__gte=timezone.now()).order_by("start").first() or series.occurrences.order_by("start").first()
            if event is None:
                raise Invalid(svc.MSG, {"frequency": ["This recurrence produces no occurrences."]})
            if notify:
                transaction.on_commit(lambda: notices.notify_series_created(series, actor=user))
        else:
            values.pop("cancelled", None)
            event = svc.create_event(user, **values)
            if notify:
                transaction.on_commit(lambda: notices.notify_event_created(event, actor=user))
    return 201, _detail(request, event)


@router.get("/{event_id}", response=EventDetailOut, summary="One event with its audience, series and line-up status", operation_id="get_event")
def get_event(request, event_id: uuid.UUID):
    return _detail(request, _event(event_id))


@router.get("/{event_id}/form", response=EventFormOut, summary="Starting values for the edit form", operation_id="event_form")
def event_form(request, event_id: uuid.UUID):
    event = _event(event_id)
    series = event.series if event.series_id else None
    values = svc.event_values(event)
    return {
        "event": values,
        "series": svc.series_values(series) if series else None,
        "series_description": str(rec.describe_recurrence(series)) if series else "",
        "can_edit": access.can_manage_event(request.user, event),
    }


def _notify_change(user, before: dict, event, *, scope: str, notify: bool) -> None:
    if not notify or event is None:
        return
    if before["cancelled"] != event.cancelled:
        what = "cancelled" if event.cancelled else "uncancelled"
    elif before["start"] != event.start:
        what = "rescheduled"
    else:
        return
    series_wide = bool(event.series_id) and scope != "this"
    transaction.on_commit(lambda: notices.notify_event_changed(event, what, actor=user, series_wide=series_wide))


@router.patch("/{event_id}", response={200: EventDetailOut, 204: None}, summary="Edit an event or (scope) its series", operation_id="update_event")
def update_event(request, event_id: uuid.UUID, payload: EventPatch, scope: str = Query("this", description=SCOPE_HELP)):
    """Only sent fields change. For an occurrence of a series, ``scope`` picks this one (it is detached from the series),
    this and the following ones (the series is split) or all of them (past occurrences keep their values). Sending
    ``recurrence`` / ``rrule`` changes the series' rule (scope ``all`` only); occurrences the new rule no longer produces
    are removed, so the response is the edited event, else the series' next occurrence, else 204. Invited people are
    told when the event is cancelled, un-cancelled or moved (``notify`` false to keep quiet)."""
    event = _event(event_id)
    values = payload.dict(exclude_unset=True)
    notify = values.pop("notify", True)
    series_keys = _recurrence(values)
    user = request.user
    before = {"start": event.start, "cancelled": event.cancelled}
    with transaction.atomic():
        if series_keys:
            if event.series_id is None:
                raise Invalid(svc.MSG, {"recurrence": ["A single event has no recurrence."]})
            if scope != "all":
                raise Invalid(svc.MSG, {"scope": ["Changing the recurrence applies to the whole series: use scope all."]})
            _refuse_one_off_fields(values)
            series = svc.update_series(user, event.series, **values, **series_keys)
            now = timezone.now()
            event = Event.objects.filter(pk=event.pk).first() or series.occurrences.filter(start__gte=now).order_by("start").first() or series.occurrences.order_by("-start").first()
            if event is None:
                return 204, None
        else:
            event = svc.update_event(user, event, scope, **values)
        _notify_change(user, before, event, scope=scope, notify=notify)
    return 200, _detail(request, event)


@router.delete("/{event_id}", response={204: None}, summary="Delete an event or (scope) part of its series", operation_id="delete_event")
def delete_event(request, event_id: uuid.UUID, scope: str = Query("this", description=SCOPE_HELP)):
    """``this``: only this occurrence; ``following``: the series ends before it; ``all``: the series and its future
    occurrences (past ones stay as one-off events)."""
    svc.delete_event(request.user, _event(event_id), scope)
    return 204, None


def _set_cancelled(request, event_id, cancelled: bool, notify: bool):
    event = _event(event_id)
    user = request.user
    before = {"start": event.start, "cancelled": event.cancelled}
    with transaction.atomic():
        event = svc.update_event(user, event, "this", cancelled=cancelled)
        _notify_change(user, before, event, scope="this", notify=notify)
    return _detail(request, event)


def _game_lineup(event, timeline, shown: dict) -> dict | None:
    """The line-up tables stored on the game's timeline, ours first: our players are linked to the roster member the name
    matched (with their goals, assists and penalty minutes in this game)."""
    from events.models import GameStat

    lineups = (timeline.data or {}).get("lineups") or {}
    our_side = "home" if event.is_home_game else "away"
    their_side = "away" if our_side == "home" else "home"
    if not lineups.get(our_side) and not lineups.get(their_side):
        return None
    by_member: dict = {}
    members: dict = {}
    for row in GameStat.objects.filter(event=event, is_ours=True).select_related("member"):
        if row.member_id is None:
            continue
        counters = by_member.setdefault(row.member_id, {"goals": 0, "assists": 0, "penalty_minutes": 0})
        if row.stat_type == GameStat.StatType.GOAL:
            counters["goals"] += row.value
        elif row.stat_type == GameStat.StatType.ASSIST:
            counters["assists"] += row.value
        elif row.stat_type == GameStat.StatType.PENALTY_MINUTES:
            counters["penalty_minutes"] += row.value
        if row.stat_type == GameStat.StatType.APPEARANCE:
            members[(row.meta or {}).get("license") or row.player_number] = row.member
    ours = []
    for entry in lineups.get(our_side) or []:
        member = members.get(entry.get("license") or entry.get("number"))
        name = f"{member.first_name} {member.last_name}".strip() if member else entry["name"]
        ours.append(
            {**entry, "member_id": member.pk if member else None, "photo_url": member.player_photo_url if member else None, "initials": "".join(part[0] for part in name.split()[:2]).upper(), **(by_member.get(member.pk, {}) if member else {})}
        )
    theirs = [{**entry, "initials": "".join(part[0] for part in entry["name"].split()[:2]).upper()} for entry in lineups.get(their_side) or []]
    return {"ours_label": shown["ours_label"], "theirs_label": shown["theirs_label"], "ours": ours, "theirs": theirs}


@router.get("/{event_id}/game-sheet", response=GameSheetOut, summary="The game sheet (goals, penalties, goalie changes) of a game", operation_id="event_game_sheet")
def game_sheet(request, event_id: uuid.UUID):
    """Everyone in the management app may read it. 404 when the game has no game sheet (yet)."""
    import json

    from events.services import game_timeline

    event = _event(event_id)
    timeline = GameTimeline.objects.filter(event=event).first()
    if timeline is None or not timeline.data:
        raise HttpError(404, "Not found.")
    out = game_timeline.display_timeline(timeline, event)
    out["lineup"] = _game_lineup(event, timeline, out)
    out = json.loads(json.dumps(out, default=str))  # lazy translations -> plain strings
    full_names = ", ".join(team.name for team in event.teams.all())
    if full_names:
        out["ours_label"] = full_names  # the scoreboard has room for the full team name
    out.update(status=timeline.status, final=timeline.finalized_at is not None, updated_at=timeline.changed_at, source=timeline.source)
    return out


# -- following games for the live pushes (opt-in, per account) ------------------------------------------------------------


@router.get("/follow-teams", response=FollowedTeamsOut, summary="The teams whose games you follow", operation_id="followed_teams")
def followed_teams(request):
    from events.services import follows

    return {"team_ids": sorted(follows.followed_team_ids(request.user))}


@router.put("/follow-teams/{team_id}", response=FollowedTeamsOut, summary="Follow a team's games (live pushes)", operation_id="follow_team")
def follow_team(request, team_id: uuid.UUID):
    from events.services import follows

    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        raise HttpError(404, "Not found.")
    follows.follow_team(request.user, team)
    return {"team_ids": sorted(follows.followed_team_ids(request.user))}


@router.delete("/follow-teams/{team_id}", response=FollowedTeamsOut, summary="Stop following a team's games", operation_id="unfollow_team")
def unfollow_team(request, team_id: uuid.UUID):
    from events.services import follows

    team = Team.objects.filter(pk=team_id).first()
    if team is not None:
        follows.unfollow_team(request.user, team)
    return {"team_ids": sorted(follows.followed_team_ids(request.user))}


@router.get("/{event_id}/follow", response=FollowStateOut, summary="Whether you follow this game", operation_id="event_follow_state")
def follow_state(request, event_id: uuid.UUID):
    from events.services import follows

    return follows.follow_state(request.user, _event(event_id))


@router.put("/{event_id}/follow", response=FollowStateOut, summary="Follow this game (live pushes)", operation_id="follow_event")
def follow_event(request, event_id: uuid.UUID):
    from events.services import follows

    event = _event(event_id)
    follows.follow_event(request.user, event)
    return follows.follow_state(request.user, event)


@router.delete("/{event_id}/follow", response=FollowStateOut, summary="Stop following this game", operation_id="unfollow_event")
def unfollow_event(request, event_id: uuid.UUID):
    from events.services import follows

    event = _event(event_id)
    follows.unfollow_event(request.user, event)
    return follows.follow_state(request.user, event)


@router.get("/{event_id}/game-moments", response=list[GameMomentOut], summary="Detected game moments and whether they were pushed (settings permission)", operation_id="event_game_moments")
def game_moments(request, event_id: uuid.UUID):
    """What the live-game detection recorded for this game, oldest first: ``state`` is ``pushed`` (delivered),
    ``catch_up`` (recorded after the fact, never pushed) or ``not_pushed`` (notifiable but not delivered, yet)."""
    from events.services.game_moments import describe_moment

    access.require_settings(request.user)
    event = _event(event_id)
    out = []
    for moment in event.moments.order_by("created"):
        state = "pushed" if moment.delivered_at else ("catch_up" if not moment.notifiable else "not_pushed")
        out.append(
            {
                "id": moment.pk,
                "key": moment.key,
                "kind": moment.kind,
                "kind_label": str(moment.get_kind_display()),
                "description": str(describe_moment(moment)),
                "game_seconds": moment.game_seconds,
                "detected_at": moment.created,
                "notifiable": moment.notifiable,
                "delivered_at": moment.delivered_at,
                "state": state,
            }
        )
    return out


@router.post("/{event_id}/update-game", response=EventDetailOut, summary="Fetch the score and game sheet of a played game from its competition", operation_id="update_game_details")
def update_game(request, event_id: uuid.UUID):
    """Score, live flag and game sheet of one played game, straight from its competition's data source. Managers of the
    event's teams (or whoever may edit it) only; 422 for a game that hasn't started or has no competition game id."""
    from events.services import game_timeline

    event = _event(event_id)
    if not (access.can_manage_event(request.user, event) or access.can_manage_attendance(request.user, event)):
        raise NotAllowed("You may not update this game.")
    if event.start > timezone.now() or not (event.competition and event.external_game_id):
        raise Invalid("Only played games imported from a competition can be updated.", {"event": ["Only played games imported from a competition can be updated."]})
    try:
        if game_timeline.fetch_game(event) is None:
            raise Invalid("This club has no data source for this game's competition.", {"event": ["No data source for this competition."]})
    except game_timeline.CompetitionFetchError as error:
        raise Invalid(str(error), {"event": [str(error)]}) from error
    return _detail(request, event)


@router.post("/{event_id}/cancel", response=EventDetailOut, summary="Cancel an event (kept, struck through)", operation_id="cancel_event")
def cancel_event(request, event_id: uuid.UUID, notify: bool = True):
    """An occurrence of a series is detached from it. Invited people are told unless ``notify`` is false."""
    return _set_cancelled(request, event_id, True, notify)


@router.post("/{event_id}/uncancel", response=EventDetailOut, summary="Undo a cancellation", operation_id="uncancel_event")
def uncancel_event(request, event_id: uuid.UUID, notify: bool = True):
    return _set_cancelled(request, event_id, False, notify)


# -- attendance --------------------------------------------------------------------------------------------------------


def _roster_info(event, member_ids) -> dict:
    """``{member_id: TeamMembership}`` (first of the event's teams) for the event's season."""
    season = attendance_svc.resolve_season(event)
    if season is None or not member_ids:
        return {}
    out = {}
    for tm in TeamMembership.objects.filter(team__in=event.teams.all(), season=season, member_id__in=member_ids).select_related("position").order_by("team__ordering", "team__name"):
        out.setdefault(tm.member_id, tm)
    return out


def _visible_selection(event, manager: bool) -> set:
    lineup = Lineup.objects.filter(event=event).first()
    if lineup is None or (not manager and lineup.published_at is None):
        return set()
    return set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))


def _latest_changes(attendance_ids) -> dict:
    """``{attendance_id: newest AttendanceChange}`` for the given attendances."""
    out = {}
    for change in AttendanceChange.objects.filter(attendance_id__in=list(attendance_ids)).order_by("created"):
        out[change.attendance_id] = change
    return out


def _attendance_row(attendance, *, manager: bool, roster: dict, selected: set, latest: dict | None = None) -> dict:
    member = attendance.member
    change = (latest or {}).get(attendance.pk) if attendance.status != Attendance.AttendanceStatus.NO_RESPONSE else None
    tm = roster.get(member.pk)
    return {
        "member": _ref(member),
        "first_name": member.first_name,
        "last_name": member.last_name,
        "status": attendance.status,
        "showed_up": attendance.showed_up,
        "note": attendance.note,
        "coach_note": attendance.coach_note if manager else None,
        "answered_by_coach": attendance.answered_by_coach,
        "answered_by_name": (change.changed_by_name or None) if change is not None else None,
        "answered_via": change.via if change is not None else None,
        "coach_set_at": attendance.coach_set_at,
        "position": tm.position.name if tm is not None and tm.position_id else "",
        "jersey_number": tm.jersey_number if tm is not None else None,
        "team_id": tm.team_id if tm is not None else None,
        "selected": member.pk in selected,
        "has_user": member.user_id is not None,
    }


def _invited_ids(event) -> set:
    return set(attendance_svc.effective_members(event).values_list("pk", flat=True))


def _attendances(event) -> list:
    """The event's attendance rows, plus (unsaved, "no response") rows for invited members who have none yet: every
    invitee of an upcoming event (e.g. while a club-wide sync is still queued), and of a past event that has no rows
    at all (recorded after the fact)."""
    rows = list(Attendance.objects.filter(event=event).select_related("member"))
    if event.start >= timezone.now() or not rows:
        have = {a.member_id for a in rows}
        missing = _invited_ids(event) - have
        rows += [Attendance(event=event, member=m) for m in Member.objects.filter(pk__in=missing)]
    rows.sort(key=lambda a: (a.member.last_name.casefold(), a.member.first_name.casefold()))
    return rows


@router.get("/{event_id}/attendance", response=AttendanceListOut, summary="Every invited member's answer and check-in", operation_id="event_attendance")
def event_attendance(request, event_id: uuid.UUID):
    """``coach_note`` is only filled in for people who manage this event's attendance (``can_manage``); ``selected``
    shows the line-up picks (to everyone else only once published)."""
    event = _event(event_id)
    manager = access.can_manage_attendance(request.user, event)
    rows = _attendances(event)
    roster = _roster_info(event, [a.member_id for a in rows])
    selected = _visible_selection(event, manager)
    latest = _latest_changes([a.pk for a in rows if a._state.adding is False])
    counts = _empty_counts()
    for a in rows:
        if a.status in counts:
            counts[a.status] += 1
        counts["total"] += 1
    return {
        "can_manage": manager,
        "counts": counts,
        "checked_in": sum(1 for a in rows if a.showed_up is True),
        "no_shows": sum(1 for a in rows if a.showed_up is False and a.status in ("present", "selected")),
        "rows": [_attendance_row(a, manager=manager, roster=roster, selected=selected, latest=latest) for a in rows],
        "blocked": _blocked(event) if manager else [],
    }


def _blocked(event) -> list[dict]:
    found = attendance_svc.blocked_members(event)
    if not found:
        return []
    people = Member.objects.filter(pk__in=found).order_by("last_name", "first_name")
    return [{"member_id": m.pk, "first_name": m.first_name, "last_name": m.last_name, "requirements": found[m.pk]} for m in people]


@router.get("/{event_id}/attendance/{member_id}/history", response=list[AttendanceChangeOut], summary="Who changed a member's answer, when (newest first)", operation_id="event_attendance_history")
def event_attendance_history(request, event_id: uuid.UUID, member_id: uuid.UUID):
    """Visible to everyone who can see the attendance list."""
    event = _event(event_id)
    member = _member(member_id)
    return list(AttendanceChange.objects.filter(attendance__event=event, attendance__member=member).order_by("-created"))


def _attendance_for(event, member, invited: set | None = None) -> Attendance:
    """The member's attendance row (created for an invited member without one); 404 when not invited."""
    attendance = Attendance.objects.filter(event=event, member=member).select_related("event", "member").first()
    if attendance is None:
        if member.pk not in (invited if invited is not None else _invited_ids(event)):
            raise HttpError(404, "This member is not invited to this event.")
        attendance = Attendance.objects.create(event=event, member=member)
        attendance.event = event
    return attendance


def _one_row(event, attendance) -> dict:
    roster = _roster_info(event, [attendance.member_id])
    return _attendance_row(attendance, manager=True, roster=roster, selected=_visible_selection(event, True), latest=_latest_changes([attendance.pk]))


@router.post("/{event_id}/attendance/bulk", response=BulkAnswerOut, summary="Set the same answer for several members (attendance manager)", operation_id="set_event_answers_bulk")
def set_answers_bulk(request, event_id: uuid.UUID, payload: BulkAnswerIn):
    """Each member is handled on its own: a refusal for one (not invited, in the published line-up, ...) is reported in
    its result and the others still change."""
    event = _event(event_id)
    _require_attendance(request, event)
    if payload.status not in attendance_svc.COACH_ANSWERS:
        raise Invalid("Unknown status.", {"status": ["Choose present, absent, maybe or no_response."]})
    invited = _invited_ids(event)
    members = Member.objects.in_bulk(list(dict.fromkeys(payload.member_ids)))
    results = []
    for member_id in dict.fromkeys(payload.member_ids):
        member = members.get(member_id)
        if member is None:
            results.append({"member_id": member_id, "ok": False, "error": "Unknown member."})
            continue
        try:
            with transaction.atomic():
                attendance = _attendance_for(event, member, invited)
                attendance = attendance_svc.set_answer_as_coach(request.user, attendance, payload.status, notify=payload.notify)
            results.append({"member_id": member_id, "ok": True, "status": attendance.status})
        except Invalid as e:
            results.append({"member_id": member_id, "ok": False, "error": str(e)})
        except HttpError as e:
            results.append({"member_id": member_id, "ok": False, "error": str(e)})
    return {"results": results}


@router.post("/{event_id}/attendance/remind", response=RemindOut, summary="Remind everyone who has not answered yet (attendance manager)", operation_id="remind_event_attendance")
def remind_attendance(request, event_id: uuid.UUID):
    event = _event(event_id)
    _require_attendance(request, event)
    return {"reminded": attendance_svc.remind_unanswered(request.user, event)}


@router.put("/{event_id}/attendance/{member_id}", response=AttendanceRowOut, summary="Set a member's answer / coach note (attendance manager)", operation_id="set_event_answer")
def set_answer(request, event_id: uuid.UUID, member_id: uuid.UUID, payload: AnswerIn):
    """``status``: present / absent / maybe / no_response (reset). ``coach_note``: omitted or null keeps it, "" clears it;
    never shown to the player. ``notify``: tell the player (inbox + push) when the answer changed."""
    event = _event(event_id)
    _require_attendance(request, event)
    member = _member(member_id)
    with transaction.atomic():
        attendance = _attendance_for(event, member)
        attendance = attendance_svc.set_answer_as_coach(request.user, attendance, payload.status, coach_note=payload.coach_note, notify=payload.notify)
    return _one_row(event, attendance)


@router.put("/{event_id}/attendance/{member_id}/check-in", response=AttendanceRowOut, summary="Record whether a member showed up (attendance manager)", operation_id="set_event_check_in")
def set_check_in(request, event_id: uuid.UUID, member_id: uuid.UUID, payload: CheckInIn):
    """``showed_up``: true, false, or null to clear the check-in."""
    event = _event(event_id)
    _require_attendance(request, event)
    member = _member(member_id)
    with transaction.atomic():
        attendance = _attendance_for(event, member)
        attendance = attendance_svc.record_check_in(request.user, attendance, payload.showed_up)
    return _one_row(event, attendance)


# -- line-up -----------------------------------------------------------------------------------------------------------


def _lineup_out(request, event, lineup=None) -> dict:
    manager = access.can_manage_attendance(request.user, event)
    lineup = lineup if lineup is not None else Lineup.objects.filter(event=event).select_related("team").first()
    teams = list(event.teams.all())
    team = lineup.team if lineup is not None else (teams[0] if len(teams) == 1 else None)
    available = lineup_svc.has_lineup(event)
    members = []
    config = lineup_svc.slots_config() if available else (False, [])
    slots = {}
    if available and team is not None:
        rows = lineup_svc.selectable_members(lineup if lineup is not None else Lineup(event=event, team=team))
        show_picks = manager or (lineup is not None and lineup.published_at is not None)
        notes, turnout = {}, {}
        if manager:
            notes = dict(Attendance.objects.filter(event=event).values_list("member_id", "note"))
            turnout = attendance_svc.member_kind_turnout([r["member"].pk for r in rows], attendance_svc.resolve_season(event))
        if show_picks:
            slots = lineup_svc.selection_slots(lineup, config)
        members = [
            {
                "member": _ref(r["member"]),
                "slots": slots.get(r["member"].pk, []) if r["selected"] else [],
                "position": r["position"],
                "jersey_number": r["jersey_number"],
                "status": r["status"],
                "available": r["available"],
                "on_roster": r["on_roster"],
                "selected": r["selected"] and show_picks,
                "note": notes.get(r["member"].pk, ""),
                "games_rate": turnout.get(r["member"].pk, {}).get("games"),
                "practices_rate": turnout.get(r["member"].pk, {}).get("practices"),
            }
            for r in rows
        ]
    return {
        "available": available,
        "can_manage": manager,
        "status": lineup_svc.status(lineup),
        "team": _ref(team) if team is not None else None,
        "teams": [_ref(t) for t in teams],
        "published_at": lineup.published_at if lineup else None,
        "scheduled_publish_at": lineup.scheduled_publish_at if lineup else None,
        "members": members,
        "slots_enabled": config[0],
        "template": config[1],
    }


def _existing_lineup(event) -> Lineup:
    lineup = Lineup.objects.filter(event=event).select_related("event", "team").first()
    if lineup is None:
        raise Invalid("Pick players before publishing.", {"member_ids": ["Pick players before publishing."]})
    return lineup


@router.get("/{event_id}/lineup", response=LineupOut, summary="The line-up and the players to pick from", operation_id="event_lineup")
def get_lineup(request, event_id: uuid.UUID):
    """Games and tournaments only (``available``). ``members``: the team's roster for the event's season with each
    one's answer and whether they can be picked. Picks of a draft are only shown to attendance managers."""
    return _lineup_out(request, _event(event_id))


@router.get("/{event_id}/lineup.pdf", summary="The line-up as a PDF overview (managers; others once it is published)", operation_id="event_lineup_pdf")
def lineup_pdf(request, event_id: uuid.UUID):
    """Header (club, event, date, location, opponent), the lines with each slot's player, who is not in a line, the number of players not
    selected and the status. Attendance managers always; everyone else only when the line-up is published (as the JSON line-up)."""
    event = _event(event_id)
    lineup = Lineup.objects.filter(event=event).select_related("event", "team").first()
    if not lineup_svc.has_lineup(event) or lineup is None or (lineup.published_at is None and not access.can_manage_attendance(request.user, event)):
        raise HttpError(404, "Not found.")
    try:
        pdf = lineup_svc.render_overview_pdf(lineup)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="lineup-{event.start:%Y-%m-%d}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.put("/{event_id}/lineup", response=LineupOut, summary="Save the picks (attendance manager)", operation_id="set_event_lineup")
def set_lineup(request, event_id: uuid.UUID, payload: LineupIn):
    """Replaces the whole pick. ``team_id`` is needed for the first save when the event has several teams. Nobody is
    told until the line-up is published."""
    event = _event(event_id)
    _require_attendance(request, event)
    team = None
    if payload.team_id is not None:
        team = Team.objects.filter(pk=payload.team_id).first()
        if team is None:
            raise Invalid("Unknown team.", {"team_id": ["Unknown team."]})
    with transaction.atomic():
        lineup = lineup_svc.get_or_create_lineup(request.user, event, team)
        slots = payload.slots
        if isinstance(slots, list):
            slots = [(item.slot, item.member_id) for item in slots]
        lineup_svc.set_selection(request.user, lineup, payload.member_ids, slots)
    return _lineup_out(request, event)


@router.post("/{event_id}/lineup/publish", response=LineupPublishOut, summary="Publish (or publish changes to) the line-up (attendance manager)", operation_id="publish_event_lineup")
def publish_lineup(request, event_id: uuid.UUID):
    """Picked players become "selected", other available invitees "not selected"; only people whose situation changed
    since the last publish are told."""
    event = _event(event_id)
    _require_attendance(request, event)
    result = lineup_svc.publish_lineup(request.user, _existing_lineup(event))
    return {"lineup": _lineup_out(request, event), "newly_selected": sorted(result["selected"], key=str), "dropped": sorted(result["dropped"], key=str)}


@router.delete("/{event_id}/lineup/publish", response=LineupOut, summary="Withdraw a published line-up: back to a draft, the picks stay (attendance manager)", operation_id="unpublish_event_lineup")
def unpublish_lineup(request, event_id: uuid.UUID):
    event = _event(event_id)
    _require_attendance(request, event)
    lineup_svc.unpublish_lineup(request.user, _existing_lineup(event))
    return _lineup_out(request, event)


@router.post("/{event_id}/lineup/schedule", response=LineupOut, summary="Publish the line-up automatically later (attendance manager)", operation_id="schedule_event_lineup")
def schedule_lineup(request, event_id: uuid.UUID, payload: LineupScheduleIn):
    event = _event(event_id)
    _require_attendance(request, event)
    when = payload.when if timezone.is_aware(payload.when) else timezone.make_aware(payload.when)
    lineup_svc.schedule_publish(request.user, _existing_lineup(event), when)
    return _lineup_out(request, event)


@router.delete("/{event_id}/lineup/schedule", response=LineupOut, summary="Cancel a scheduled publish (attendance manager)", operation_id="unschedule_event_lineup")
def unschedule_lineup(request, event_id: uuid.UUID):
    event = _event(event_id)
    _require_attendance(request, event)
    lineup = Lineup.objects.filter(event=event).select_related("event").first()
    if lineup is not None:
        lineup_svc.unschedule(request.user, lineup)
    return _lineup_out(request, event)


# -- tasks -------------------------------------------------------------------------------------------------------------


def _task_out(task, manager: bool) -> dict:
    return {
        "id": task.pk,
        "title": task.title,
        "description": task.description,
        "needed_quantity": task.needed_quantity,
        "claimed_count": task.claimed_count,
        "open_slots": task.open_slots,
        "claimed_by_me": task.claimed_by_me,
        "claims": [{"id": claim.pk, "label": label, "member_id": claim.member_id if manager else None, "member_name": claim.member.get_full_name() if manager else None} for claim, label in zip(task.claim_list, task.claim_labels, strict=True)],
        "created_by": _ref(task.created_by) if task.created_by_id else None,
    }


def _one_task(request, event, task_id) -> dict:
    manager = task_svc.can_manage_tasks(request.user, event)
    for task in task_svc.list_tasks(request.user, event):
        if task.pk == task_id:
            return _task_out(task, manager)
    raise HttpError(404, "Not found.")


def _task(event, task_id) -> EventTask:
    task = EventTask.objects.filter(pk=task_id, event=event).select_related("event").first()
    if task is None:
        raise HttpError(404, "Not found.")
    return task


@router.get("/{event_id}/tasks", response=TaskListOut, summary="The event's tasks and who took them", operation_id="event_tasks")
def list_tasks(request, event_id: uuid.UUID):
    """Claims read as "<Family> family" (or the member's name); ``member_id`` / ``member_name`` only for task managers."""
    event = _event(event_id)
    manager = task_svc.can_manage_tasks(request.user, event)
    return {"can_manage": manager, "tasks": [_task_out(t, manager) for t in task_svc.list_tasks(request.user, event)]}


@router.post("/{event_id}/tasks", response={201: TaskOut}, summary="Add a task (attendance manager)", operation_id="create_event_task")
def create_task(request, event_id: uuid.UUID, payload: TaskIn):
    """``notify``: tell the event's invited people with a login about the new task."""
    event = _event(event_id)
    task = task_svc.create_task(request.user, event, title=payload.title, description=payload.description, needed_quantity=payload.needed_quantity, notify=payload.notify)
    return 201, _one_task(request, event, task.pk)


@router.patch("/{event_id}/tasks/{task_id}", response=TaskOut, summary="Edit a task (attendance manager)", operation_id="update_event_task")
def update_task(request, event_id: uuid.UUID, task_id: uuid.UUID, payload: TaskPatch):
    event = _event(event_id)
    task_svc.update_task(request.user, _task(event, task_id), **payload.dict(exclude_unset=True))
    return _one_task(request, event, task_id)


@router.delete("/{event_id}/tasks/{task_id}", response={204: None}, summary="Delete a task (attendance manager)", operation_id="delete_event_task")
def delete_task(request, event_id: uuid.UUID, task_id: uuid.UUID):
    """The people who had claimed it are told it is no longer needed."""
    event = _event(event_id)
    task_svc.delete_task(request.user, _task(event, task_id))
    return 204, None


@router.post("/{event_id}/tasks/{task_id}/claim", response=TaskOut, summary="Take a slot of a task", operation_id="claim_event_task")
def claim_task(request, event_id: uuid.UUID, task_id: uuid.UUID, member_id: uuid.UUID | None = None):
    """For yourself (your own member record), or with ``member_id`` on someone's behalf (attendance managers; that
    member is told). ``member_id`` as a query parameter or in an optional JSON body ``{"member_id": ...}``. 422 when
    already claimed or full."""
    event = _event(event_id)
    task = _task(event, task_id)
    if member_id is None and request.body:
        try:
            member_id = ClaimIn.model_validate_json(request.body).member_id
        except ValueError:
            raise Invalid("Invalid body.", {"member_id": ["Not a valid member id."]}) from None
    member = _member(member_id) if member_id else None
    task_svc.claim_task(request.user, task, member)
    return _one_task(request, event, task_id)


@router.delete("/{event_id}/tasks/{task_id}/claim", response={204: None}, summary="Give a slot back", operation_id="unclaim_event_task")
def unclaim_task(request, event_id: uuid.UUID, task_id: uuid.UUID, member_id: uuid.UUID | None = None):
    """Your own claim, or with ``member_id`` someone else's (attendance managers). Safe to repeat."""
    event = _event(event_id)
    task = _task(event, task_id)
    member = _member(member_id) if member_id else None
    task_svc.unclaim_member(request.user, task, member)
    return 204, None


# -- attachments -------------------------------------------------------------------------------------------------------


def _attachment_out(a) -> dict:
    return {
        "id": a.pk,
        "name": a.name,
        "size": a.size,
        "uploaded_by": _ref(a.uploaded_by) if a.uploaded_by_id else None,
        "created": a.created,
        "download_url": f"/api/v1/events/{a.event_id}/attachments/{a.pk}/download",
    }


def _attachment(event, attachment_id) -> EventAttachment:
    a = EventAttachment.objects.filter(pk=attachment_id, event=event).select_related("event", "uploaded_by").first()
    if a is None:
        raise HttpError(404, "Not found.")
    return a


@router.get("/{event_id}/attachments", response=AttachmentListOut, summary="Files attached to the event", operation_id="event_attachments")
def list_attachments(request, event_id: uuid.UUID):
    event = _event(event_id)
    return {"can_manage": attachment_svc.can_manage_attachments(request.user, event), "attachments": [_attachment_out(a) for a in attachment_svc.list_attachments(request.user, event)]}


@router.post("/{event_id}/attachments", response={201: list[AttachmentOut]}, summary="Upload files (attendance manager)", operation_id="add_event_attachments")
def add_attachments(request, event_id: uuid.UUID, files: File[list[UploadedFile]]):
    """Multipart, one or more ``files`` fields: documents and pictures, at most 10 MB each and 20 per event. All or
    nothing: one bad file stores none (422)."""
    event = _event(event_id)
    return 201, [_attachment_out(a) for a in attachment_svc.add_attachments(request.user, event, files)]


@router.get("/{event_id}/attachments/{attachment_id}/download", summary="Download a file (always as an attachment)", operation_id="download_event_attachment")
def download_attachment(request, event_id: uuid.UUID, attachment_id: uuid.UUID):
    attachment = _attachment(_event(event_id), attachment_id)
    try:
        handle = attachment_svc.open_attachment(attachment, actor=request.user)
    except FileNotFoundError:
        raise HttpError(404, "The file is gone.") from None
    response = FileResponse(handle, as_attachment=True, filename=attachment.name, content_type=attachment_svc.DOWNLOAD_CONTENT_TYPE)
    for header, value in attachment_svc.DOWNLOAD_HEADERS.items():
        response[header] = value
    return response


@router.delete("/{event_id}/attachments/{attachment_id}", response={204: None}, summary="Delete a file (attendance manager)", operation_id="delete_event_attachment")
def delete_attachment(request, event_id: uuid.UUID, attachment_id: uuid.UUID):
    attachment_svc.delete_attachment(request.user, _attachment(_event(event_id), attachment_id))
    return 204, None
