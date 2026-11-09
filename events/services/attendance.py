"""Attendance: keep an event's attendance rows in sync with its audience, coach answers and check-ins, statistics.

The audience of an event is the union of the rosters of its ``teams`` (for the event's season) and the members of its
``groups``, plus the individually ``invited_members``, minus the ``excluded_members`` (exclusion wins). A ``club_wide``
event instead invites every member with an ACTIVE ``ClubMembership`` in the event's season (plus invited, minus
excluded). An event without a ``season`` uses the season covering its start date.

Attendance rows are reconciled against that set only for events that are still in the future: history is never
rewritten. Reconciling is done in bulk (a fixed number of queries however many events or members are involved), see
``sync_attendances``; the signals in ``events/signals.py`` call it whenever a roster, group, club membership or an
event's audience changes.

Not ported from the old app: the "open onboarding requirement blocks this event kind" exclusion (there is no
``blocked_member_ids_for_event`` equivalent in ``members/services/onboarding.py`` yet) and member self-service RSVP.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable

from django.db import connection, transaction
from django.db.models import Count, F, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from events.models import Attendance, AttendanceChange, Event, Season
from members.models import ClubMembership, GroupMembership, Member
from members.services import access, audit
from members.services.errors import Invalid, NotAllowed

Status = Attendance.AttendanceStatus

COACH_ANSWERS = ("present", "absent", "maybe", "no_response")
COACH_NOTE_MAX = 500
ANSWER_LABELS = {"present": "In", "absent": "Out", "maybe": "Maybe", "no_response": "No answer"}


# --- seasons -------------------------------------------------------------------------------------------------------


def _event_date(event) -> datetime.date:
    return timezone.localtime(event.start).date() if timezone.is_aware(event.start) else event.start.date()


def resolve_season(event) -> Season | None:
    """The season whose rosters define the event's audience: its own ``season``, else the one covering its start date."""
    if event.season_id is not None:
        return event.season
    return Season.get_current(_event_date(event))


def _season_resolver():
    """A function ``event -> season id`` that loads the (small) season table once instead of once per event."""
    seasons = list(Season.objects.order_by("start_date").values_list("pk", "start_date", "end_date"))

    def resolve(event):
        if event.season_id is not None:
            return event.season_id
        day = _event_date(event)
        for pk, start, end in seasons:
            if start <= day <= end:
                return pk
        return None

    return resolve


def _in_season(season: Season, prefix: str = "event__") -> Q:
    """Events of ``season``: linked to it, or without a season and starting inside its dates."""
    return Q(**{f"{prefix}season": season}) | Q(**{f"{prefix}season__isnull": True, f"{prefix}start__date__gte": season.start_date, f"{prefix}start__date__lte": season.end_date})


def _season(season):
    return season if season is not None else Season.get_current()


# --- audience ------------------------------------------------------------------------------------------------------


def _desired_member_ids(events: list, member_ids: set | None = None) -> dict:
    """``{event.pk: {member ids}}``: the effective audience of every event in ``events``, restricted to ``member_ids``
    when given. A fixed number of queries regardless of how many events there are."""
    if not events:
        return {}
    event_ids = [event.pk for event in events]
    resolve = _season_resolver()
    season_of = {event.pk: resolve(event) for event in events}

    def only(qs, field="member_id"):
        return qs.filter(**{f"{field}__in": member_ids}) if member_ids is not None else qs

    teams_by_event, groups_by_event = defaultdict(set), defaultdict(set)
    for event_id, team_id in Event.teams.through.objects.filter(event_id__in=event_ids).values_list("event_id", "team_id"):
        teams_by_event[event_id].add(team_id)
    for event_id, group_id in Event.groups.through.objects.filter(event_id__in=event_ids).values_list("event_id", "group_id"):
        groups_by_event[event_id].add(group_id)
    invited, excluded = defaultdict(set), defaultdict(set)
    for event_id, member_id in only(Event.invited_members.through.objects.filter(event_id__in=event_ids)).values_list("event_id", "member_id"):
        invited[event_id].add(member_id)
    for event_id, member_id in only(Event.excluded_members.through.objects.filter(event_id__in=event_ids)).values_list("event_id", "member_id"):
        excluded[event_id].add(member_id)

    club_wide_seasons = {season_of[e.pk] for e in events if e.club_wide and season_of[e.pk]}
    team_ids = {t for e in events if not e.club_wide for t in teams_by_event[e.pk]}
    group_ids = {g for e in events if not e.club_wide for g in groups_by_event[e.pk]}
    team_seasons = {season_of[e.pk] for e in events if not e.club_wide and season_of[e.pk]}

    roster = defaultdict(set)  # (team, season) -> members
    if team_ids and team_seasons:
        from teams.models import TeamMembership

        for team_id, season_id, member_id in only(TeamMembership.objects.filter(team_id__in=team_ids, season_id__in=team_seasons)).values_list("team_id", "season_id", "member_id"):
            roster[(team_id, season_id)].add(member_id)
    group_members = defaultdict(set)
    if group_ids:
        # Members who needn't respond (e.g. a training group's admins) still see the events but get no attendance row.
        for group_id, member_id in only(GroupMembership.objects.filter(group_id__in=group_ids, must_respond=True)).values_list("group_id", "member_id"):
            group_members[group_id].add(member_id)
    active = defaultdict(set)
    if club_wide_seasons:
        for season_id, member_id in only(ClubMembership.objects.filter(season_id__in=club_wide_seasons, status=ClubMembership.StatusChoices.ACTIVE)).values_list("season_id", "member_id"):
            active[season_id].add(member_id)

    desired = {}
    for event in events:
        season_id = season_of[event.pk]
        ids = set()
        if event.club_wide:
            if season_id:
                ids |= active[season_id]
        else:
            if season_id:
                for team_id in teams_by_event[event.pk]:
                    ids |= roster[(team_id, season_id)]
            for group_id in groups_by_event[event.pk]:
                ids |= group_members[group_id]
        ids |= invited[event.pk]
        ids -= excluded[event.pk]
        desired[event.pk] = ids
    return _without_blocked(events, season_of, desired)


def _without_blocked(events: list, season_of: dict, desired: dict) -> dict:
    """Drop members whose open onboarding requirements block this kind of event (see ``onboarding.blocked_member_ids``)."""
    from members.services import onboarding

    groups: dict[tuple, set] = defaultdict(set)
    for event in events:
        groups[(event.kind, season_of[event.pk])] |= desired[event.pk]
    blocked = onboarding.blocked_member_ids(groups)
    return {event.pk: desired[event.pk] - blocked[(event.kind, season_of[event.pk])] for event in events}


def effective_members(event):
    """The ``Member`` queryset invited to ``event`` (see the module docstring for the rules)."""
    return Member.objects.filter(pk__in=_desired_member_ids([event]).get(event.pk, set()))


def sync_attendances(events: Iterable, *, member_ids: Iterable | None = None) -> list[tuple]:
    """Reconcile the attendance rows of every future event in ``events`` with its audience, optionally only for the
    members in ``member_ids``. Newly invited people get a ``NO_RESPONSE`` row; people no longer invited lose theirs
    (hard reconcile, answers included). Past events are left alone. Returns the ``(event_id, member_id)`` pairs added."""
    now = timezone.now()
    events = [event for event in events if event.start >= now]
    if not events:
        return []
    member_ids = set(member_ids) if member_ids is not None else None
    if member_ids is not None and not member_ids:
        return []
    desired = _desired_member_ids(events, member_ids)
    existing = Attendance.objects.filter(event_id__in=[e.pk for e in events])
    if member_ids is not None:
        existing = existing.filter(member_id__in=member_ids)
    have = defaultdict(dict)
    for pk, event_id, member_id in existing.values_list("pk", "event_id", "member_id"):
        have[event_id][member_id] = pk

    to_add, to_remove = [], []
    for event in events:
        want, got = desired[event.pk], have[event.pk]
        to_add.extend((event.pk, member_id) for member_id in want - got.keys())
        to_remove.extend(pk for member_id, pk in got.items() if member_id not in want)
    if to_add:
        Attendance.objects.bulk_create([Attendance(event_id=event_id, member_id=member_id) for event_id, member_id in to_add], ignore_conflicts=True)
    if to_remove:
        Attendance.objects.filter(pk__in=to_remove).delete()
    return to_add


def sync_event_attendances(event) -> None:
    """Reconcile ``event``'s attendance rows with its effective audience. No-op for events that have already started."""
    sync_attendances([event])


def future_events(**filters) -> list:
    """Upcoming events (start in the future) matching ``filters``, without duplicates."""
    return list(Event.objects.filter(start__gte=timezone.now(), **filters).distinct())


# --- notifications -------------------------------------------------------------------------------------------------


def current_club():
    """The club whose schema is active, or ``None`` on the public schema. ``connection.tenant`` is a stand-in object
    inside ``schema_context``, so look the real club up by schema name then."""
    from django_tenants.utils import get_public_schema_name

    from club.models import Club

    schema = getattr(connection, "schema_name", None)
    if not schema or schema == get_public_schema_name():
        return None
    tenant = getattr(connection, "tenant", None)
    if isinstance(tenant, Club) and tenant.pk:
        return tenant
    return Club.objects.filter(schema_name=schema).first()


def _notify(member: Member, *, title: str, body: str, href: str, category: str) -> None:
    """The member and, for a child, the parents (on the child's behalf)."""
    club = current_club()
    if club is None:
        return
    from members.services import recipients
    from notifications import inbox

    recipients.deliver(member, club, title=title, body=body, kind=inbox.Kind.INFO, href=href, push=True, category=category)


def notify_newly_invited(member: Member, events: list, *, via: str = "") -> None:
    """ONE inbox notification (and push) summarising the upcoming events that were just put on ``member``'s calendar,
    never one per event (joining a team mid-season with a whole practice series scheduled would be a flood)."""
    events = [event for event in events if not event.cancelled]
    if not events:
        return
    count = len(events)
    noun = "event has" if count == 1 else "events have"
    prefix = f"You were added to {via}. " if via else ""
    body = f"{prefix}{count} new {noun} been added to your calendar."
    if count == 1:
        body += f" {events[0].title}, {timezone.localtime(events[0].start):%d %b %Y %H:%M}."
    _notify(member, title="New events on your calendar", body=body, href="/calendar", category="event")


# --- coach answers and check-ins -----------------------------------------------------------------------------------


def _actor_member(actor) -> Member | None:
    if actor is None or not getattr(actor, "pk", None):
        return None
    return Member.objects.filter(user=actor).first()


def change_via(actor, member: Member) -> str:
    """How ``actor`` came to change ``member``'s answer: the member's own account, one of their guardians, a coach, or
    the system (no actor)."""
    if actor is None or not getattr(actor, "pk", None):
        return AttendanceChange.Via.SYSTEM
    if member.user_id is not None and member.user_id == actor.pk:
        return AttendanceChange.Via.SELF
    from members.services import recipients

    actor_member = _actor_member(actor)
    if actor_member is not None and any(g.pk == actor_member.pk for g in recipients.guardians_of(member)):
        return AttendanceChange.Via.GUARDIAN
    return AttendanceChange.Via.COACH


def record_status_change(actor, attendance: Attendance, from_status: str, to_status: str, *, note_changed: bool = False) -> AttendanceChange | None:
    """Log a status change (nothing when the status did not change)."""
    if from_status == to_status:
        return None
    via = change_via(actor, attendance.member)
    name, user = "", None
    if via != AttendanceChange.Via.SYSTEM:
        user = actor
        name = str(_actor_member(actor) or actor.get_full_name() or actor)
    return AttendanceChange.objects.create(attendance=attendance, from_status=from_status, to_status=to_status, changed_by=user, changed_by_name=name, via=via, note_changed=note_changed)


def require_attendance_manager(actor, event) -> None:
    if not access.can_manage_attendance(actor, event):
        raise NotAllowed("You are not allowed to manage the attendance of this event.")


@transaction.atomic
def set_answer_as_coach(actor, attendance: Attendance, status: str | None, coach_note: str | None = None, notify: bool = False) -> Attendance:
    """A manager sets ``attendance``'s answer on the player's behalf and/or writes the private coach note (``None``
    leaves the note alone, ``""`` clears it).

    Unlike a player's own answer the reply deadline and the event's start don't stop it (a coach corrects answers
    after the fact), but a cancelled event can't be changed. The rules:

    * ``present`` / ``maybe`` / ``absent`` set that answer; ``no_response`` resets it (and the "answered by coach" mark
      with it). ``present`` and ``no_response`` drop the player's own reason (``note``).
    * A player SELECTED in the published line-up can only be marked ``absent`` (their line-up pick stays for the coach
      to swap). ``present`` just keeps them in; ``maybe``/``no_response`` are refused rather than silently dropping them
      from the line-up. A NOT_SELECTED player marked ``present`` keeps the line-up's verdict.
    * The player is told (inbox + push, category "coach") only when ``notify`` is set and the answer changed; they never
      see ``coach_note``.
    """
    event = attendance.event
    require_attendance_manager(actor, event)
    if status is None and coach_note is None:
        raise Invalid("Nothing to change.", {"status": ["Give an answer or a coach note."]})
    if status is not None and status not in COACH_ANSWERS:
        raise Invalid("Unknown status.", {"status": ["Choose present, absent, maybe or no_response."]})
    if event.cancelled:
        raise Invalid(f"“{event.title}” is cancelled, so answers can no longer be changed.", {"__all__": ["The event is cancelled."]})
    if coach_note is not None and len(coach_note.strip()) > COACH_NOTE_MAX:
        raise Invalid(f"The coach note can be at most {COACH_NOTE_MAX} characters.", {"coach_note": [f"At most {COACH_NOTE_MAX} characters."]})

    was = attendance.status
    in_lineup = was == Status.SELECTED
    if in_lineup and status in ("maybe", "no_response"):
        raise Invalid("This player is in the published line-up: remove them from it first, or mark them absent.", {"status": ["The player is in the published line-up."]})

    before = {"status": attendance.status, "note": attendance.note, "coach_note": attendance.coach_note, "answered_by_coach": attendance.answered_by_coach}
    if status is None:
        pass  # only the coach note changes; the answer stays as it is
    elif was in (Status.SELECTED, Status.NOT_SELECTED) and status == "present":
        pass  # a published line-up already says they're available; keep its verdict
    else:
        attendance.status = status
        if status in ("present", "no_response"):
            attendance.note = ""
        attendance.answered_by_coach = status != "no_response"
    attendance.coach_set_by = _actor_member(actor)
    attendance.coach_set_at = timezone.now()
    if coach_note is not None:
        attendance.coach_note = coach_note.strip()
    attendance.save()
    record_status_change(actor, attendance, was, attendance.status, note_changed=attendance.note != before["note"])

    after = {"status": attendance.status, "note": attendance.note, "coach_note": attendance.coach_note, "answered_by_coach": attendance.answered_by_coach}
    changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    audit.record(actor, "attendance.coach_answer", member=attendance.member, target=attendance, changes=changes, target_label=f"{event.title} - {attendance.member}")

    if in_lineup and status == "absent":
        from events.services.lineup import notify_coach_dropout  # lineup imports nothing from here; keep it lazy anyway

        notify_coach_dropout(event, attendance.member, actor)  # the event's OTHER managers hear about it

    if notify and attendance.status != was:
        label = ANSWER_LABELS[status]
        _notify(attendance.member, title="Your answer was changed", body=f"Your coach set your answer for {event.title} to “{label}”.", href=f"/calendar/events/{event.pk}", category="coach")
    return attendance


@transaction.atomic
def record_check_in(actor, attendance: Attendance, showed_up: bool | None) -> Attendance:
    """Record whether the member actually showed up (``None`` clears the check-in), separate from their answer."""
    event = attendance.event
    require_attendance_manager(actor, event)
    if showed_up not in (True, False, None):
        raise Invalid("showed_up must be true, false or null.", {"showed_up": ["Invalid value."]})
    if event.cancelled:
        raise Invalid(f"“{event.title}” is cancelled.", {"__all__": ["The event is cancelled."]})
    was = attendance.showed_up
    attendance.showed_up = showed_up
    attendance.save(update_fields=["showed_up", "modified"])
    if was != showed_up:
        audit.record(actor, "attendance.check_in", member=attendance.member, target=attendance, changes={"showed_up": {"from": was, "to": showed_up}}, target_label=f"{event.title} - {attendance.member}")
    return attendance


# --- statistics ----------------------------------------------------------------------------------------------------
# Turnout everywhere is present / (present + absent) over past events, as the old app defined it: only the PRESENT
# status counts as present (SELECTED / NOT_SELECTED, EXCUSED, MAYBE and NO_RESPONSE are left out of the rate).


def _rate(present: int, absent: int) -> int | None:
    answered = present + absent
    return round(100 * present / answered) if answered else None


def _past(season, **filters):
    return Attendance.objects.filter(_in_season(season), event__start__lt=timezone.now(), **filters)


def _present_absent(qs) -> dict:
    return qs.aggregate(present=Count("id", filter=Q(status=Status.PRESENT)), absent=Count("id", filter=Q(status=Status.ABSENT)))


def team_attendance_rate(team, season: Season | None = None) -> int | None:
    """Turnout (percentage) of ``team``'s past events in ``season`` (default: current); ``None`` without answers."""
    season = _season(season)
    if season is None:
        return None
    counts = _present_absent(_past(season, event__teams=team))
    return _rate(counts["present"], counts["absent"])


def club_attendance_rate(season: Season | None = None) -> int | None:
    """Turnout (percentage) over every past event in ``season`` (default: current); ``None`` without answers."""
    season = _season(season)
    if season is None:
        return None
    counts = _present_absent(_past(season))
    return _rate(counts["present"], counts["absent"])


def player_attendance_rankings(team, season: Season | None = None, *, minimum_responses: int = 3) -> list[dict]:
    """Each player's turnout on ``team``'s past events, best first: ``[{"member", "rate", "responses"}, ...]``. Players
    with fewer than ``minimum_responses`` present/absent answers are left out (one absence out of one invite would read
    as "0%, worst on the team"). The head of the list is the top attenders, the tail the ones needing attention."""
    season = _season(season)
    if season is None:
        return []
    rows = list(_past(season, event__teams=team, status__in=[Status.PRESENT, Status.ABSENT]).values("member").annotate(present=Count("id", filter=Q(status=Status.PRESENT)), responses=Count("id")).filter(responses__gte=minimum_responses).order_by())
    members_by_id = Member.objects.in_bulk([row["member"] for row in rows])
    rankings = [{"member": members_by_id[row["member"]], "rate": round(100 * row["present"] / row["responses"]), "responses": row["responses"]} for row in rows]
    rankings.sort(key=lambda entry: (-entry["rate"], -entry["responses"], entry["member"].last_name, entry["member"].first_name))
    return rankings


def players_who_missed_recent_practices(team, season: Season | None = None, *, count: int = 2):
    """Members absent or silent (not excused) on *every one* of ``team``'s ``count`` most recent past trainings in
    ``season``. Empty when the team has fewer than ``count`` past trainings (not enough history to call anyone out)."""
    season = _season(season)
    if season is None:
        return Member.objects.none()
    practices = list(Event.objects.filter(_in_season(season, prefix=""), teams=team, kind=Event.EventKind.TRAINING, start__lt=timezone.now()).order_by("-start").values_list("pk", flat=True)[:count])
    if len(practices) < count:
        return Member.objects.none()
    missed = Attendance.objects.filter(event_id__in=practices, status__in=[Status.ABSENT, Status.NO_RESPONSE]).values("member").annotate(n=Count("id")).filter(n=len(practices)).values("member")
    return Member.objects.filter(pk__in=missed).order_by("last_name", "first_name")


def team_no_shows(team, season: Season | None = None):
    """Attendance rows of ``team``'s events where the member answered present/selected but was checked in as not there
    (``showed_up=False``), most recent first. Never inferred from a missing check-in."""
    season = _season(season)
    if season is None:
        return Attendance.objects.none()
    return Attendance.objects.filter(_in_season(season), event__teams=team, status__in=[Status.PRESENT, Status.SELECTED], showed_up=False).select_related("member", "event").order_by("-event__start")


def member_attendance_sparkline(member, season: Season | None = None, *, limit: int = 12) -> list[dict]:
    """The member's ``limit`` most recent PAST attendance rows in ``season`` (topped up with the next upcoming events when there
    are fewer), oldest first, each as
    ``{"event", "state"}`` with state "present" (answered present or was selected) / "absent" (anything else, for a past event)
    / "upcoming". Cancelled events are left out."""
    season = _season(season)
    if season is None:
        return []
    now = timezone.now()
    base = Attendance.objects.filter(_in_season(season), member=member, event__cancelled=False).select_related("event")
    # The most recent PAST events; only when the season has fewer than ``limit`` of them are the next upcoming ones added
    # (the furthest future events must never crowd out the games that were actually played).
    rows = list(base.filter(event__start__lte=now).order_by("-event__start")[:limit])
    rows.reverse()
    if len(rows) < limit:
        rows += list(base.filter(event__start__gt=now).order_by("event__start")[: limit - len(rows)])
    bars = []
    for row in rows:
        if row.event.start > now:
            state = "upcoming"
        elif row.status in (Status.PRESENT, Status.SELECTED):  # picked for the game: they were there
            state = "present"
        else:
            state = "absent"
        bars.append({"event": row.event, "state": state})
    return bars


def member_attendance_counts(member, season: Season | None = None) -> dict:
    """Season totals over past events: ``present``, ``absent``, ``no_reply`` (answer status) and ``no_shows`` (answered
    present/selected but checked in as not there)."""
    season = _season(season)
    if season is None:
        return {"present": 0, "absent": 0, "no_reply": 0, "no_shows": 0, "excused": 0, "maybe": 0, "selected": 0}
    return _past(season, member=member).aggregate(
        present=Count("id", filter=Q(status=Status.PRESENT)),
        absent=Count("id", filter=Q(status=Status.ABSENT)),
        no_reply=Count("id", filter=Q(status=Status.NO_RESPONSE)),
        excused=Count("id", filter=Q(status=Status.EXCUSED)),
        maybe=Count("id", filter=Q(status=Status.MAYBE)),
        selected=Count("id", filter=Q(status=Status.SELECTED)),
        no_shows=Count("id", filter=Q(status__in=[Status.PRESENT, Status.SELECTED], showed_up=False)),
    )


def _empty_summary() -> dict:
    return {"events": 0, "present": 0, "absent": 0, "excused": 0, "maybe": 0, "no_response": 0, "selected": 0, "not_selected": 0, "no_shows": 0, "rate": None}


def members_attendance_summary(members, season: Season | None = None, *, last: int | None = 10, include_cancelled: bool = False) -> dict:
    """``{member_id: summary}`` for every member in ``members`` (members or ids), over each one's ``last`` most recent
    past events in ``season`` (default: current; ``last=None`` for the whole season). Cancelled events are left out
    unless ``include_cancelled``. A summary counts ``events``, ``present``, ``absent``, ``excused``, ``maybe``,
    ``no_response``, ``selected``, ``not_selected`` and ``no_shows`` and has ``rate`` = present / (present + absent) as
    a percentage (``None`` without such answers). Members without any rows get an all-zero summary. One query."""
    ids = [getattr(m, "pk", m) for m in members]
    result = {member_id: _empty_summary() for member_id in ids}
    season = _season(season)
    if season is None or not ids:
        return result
    qs = _past(season, member_id__in=ids)
    if not include_cancelled:
        qs = qs.filter(event__cancelled=False)
    qs = qs.annotate(rn=Window(RowNumber(), partition_by=[F("member_id")], order_by=[F("event__start").desc(), F("event_id").desc()]))
    if last is not None:
        qs = qs.filter(rn__lte=last)
    keys = {Status.PRESENT: "present", Status.ABSENT: "absent", Status.EXCUSED: "excused", Status.MAYBE: "maybe", Status.NO_RESPONSE: "no_response", Status.SELECTED: "selected", Status.NOT_SELECTED: "not_selected"}
    for member_id, status, showed_up in qs.values_list("member_id", "status", "showed_up"):
        summary = result[member_id]
        summary["events"] += 1
        summary[keys[status]] += 1
        if showed_up is False and status in (Status.PRESENT, Status.SELECTED):
            summary["no_shows"] += 1
    for summary in result.values():
        summary["rate"] = _rate(summary["present"], summary["absent"])
    return result
