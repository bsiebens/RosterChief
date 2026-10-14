"""Presentation helpers for the member app's Home, Calendar and Event sheet -- the shapes the
templates render, derived from the rows the shared services return (mobile.services.home,
events.services.agenda, events.services.member_view). Nothing here decides who may do what:
the answering rules stay in events.services.rsvp / tasks / referees / officials.

Mirrors the iOS app's CalendarView / EventReplySheet / HomeView so a row reads the same on
both: one card per event (siblings share it), a coloured kind bar, Home/Away, the final score
of a finished game, one In/Out line per person and role, and a "x of y tasks open" pill.
"""

import collections
import datetime
import itertools
from urllib.parse import quote

from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Attendance, Event, EventTask, EventTaskClaim, RefereeSignup
from events.services.calendar import month_bounds, month_grid

AttendanceStatus = Attendance.AttendanceStatus
EventKind = Event.EventKind

#: Per event kind, the colour of the row's bar / the eyebrow badge, and the user-facing word.
#: Same mapping as the iOS ``Event.kindColor``: the club's own accent for games, a fixed colour
#: for everything else so all six read apart at a glance.
KIND_STYLES = {
    EventKind.GAME: {"bar": "bg-club", "badge": "bg-club text-club-content", "label": _("Game"), "filter_label": _("Games")},
    EventKind.TOURNAMENT: {"bar": "bg-warn", "badge": "bg-warn text-night", "label": _("Tournament"), "filter_label": _("Tournaments")},
    EventKind.TRAINING: {"bar": "bg-info", "badge": "bg-info text-night", "label": _("Practice"), "filter_label": _("Practices")},
    EventKind.MEETING: {"bar": "bg-info-text", "badge": "bg-info-text text-surface", "label": _("Meeting"), "filter_label": _("Meetings")},
    EventKind.SOCIAL: {"bar": "bg-ok", "badge": "bg-ok text-white", "label": _("Social"), "filter_label": _("Social")},
    EventKind.OTHER: {"bar": "bg-slate", "badge": "bg-slate text-surface", "label": _("Other"), "filter_label": _("Other")},
}

#: The order the filter menu lists the kinds in.
KIND_ORDER = [EventKind.GAME, EventKind.TOURNAMENT, EventKind.TRAINING, EventKind.MEETING, EventKind.SOCIAL, EventKind.OTHER]

GAME_LIKE_KINDS = (EventKind.GAME, EventKind.TOURNAMENT)

#: Referee / official sign-up rows (the ones the person answers with Accept / Decline).
SIGNUP_TYPES = ("referee", "official")


def kind_style(kind) -> dict:
    return KIND_STYLES.get(kind, KIND_STYLES[EventKind.OTHER])


def is_game_like(event) -> bool:
    return event.kind in GAME_LIKE_KINDS


def is_finished_game(event, now=None) -> bool:
    """A game that is over and has a result: shown as its final score instead of a status pill."""
    now = now or timezone.now()
    return event.kind == EventKind.GAME and not event.is_live and event.score_for is not None and event.score_against is not None and event.start <= now


def person_name(person, me) -> str:
    """ "You" for the account's own person, else the first name (the iOS answer lines' wording)."""
    if me is not None and person.pk == me.pk:
        return str(_("You"))
    return person.first_name


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def task_counts(event_ids) -> dict:
    """``{event_id: (open, total)}``: how many of an event's tasks still have a free slot, and
    how many it has in all (the same "open" Home's open-tasks card uses). Events without
    tasks are absent. (Same rule as the app API's calendar rows.)"""
    counts: dict = {}
    tasks = EventTask.objects.filter(event_id__in=set(event_ids)).annotate(claim_count=Count("claims")).values_list("event_id", "claim_count", "needed_quantity")
    for event_id, claims, needed in tasks:
        open_count, total = counts.get(event_id, (0, 0))
        counts[event_id] = (open_count + (claims < needed), total + 1)
    return counts


def household_tasks(me, club, event_ids) -> dict:
    """``{event_id: [{"id", "title", "claimed_by_me"}]}``: the tasks on these events that the
    account's household has taken -- claimed by the account's own person or anyone sharing a
    family with them (a claim is made as the account's own person, but covers the household)."""
    if me is None:
        return {}
    household = {me.pk, *me.family_members(club).values_list("pk", flat=True)}
    claims = EventTaskClaim.objects.filter(task__event_id__in=set(event_ids), member_id__in=household).select_related("task").order_by("task__created_at", "task__title")
    by_event: dict = {}
    for claim in claims:
        tasks = by_event.setdefault(claim.task.event_id, {})
        row = tasks.setdefault(claim.task_id, {"id": claim.task_id, "title": claim.task.title, "claimed_by_me": False})
        row["claimed_by_me"] = row["claimed_by_me"] or claim.member_id == me.pk
    return {event_id: list(tasks.values()) for event_id, tasks in by_event.items()}


# ---------------------------------------------------------------------------
# Status pills
# ---------------------------------------------------------------------------

_RSVP_PILLS = {
    AttendanceStatus.PRESENT: (_("In"), "pill-ok"),
    AttendanceStatus.SELECTED: (_("In"), "pill-ok"),
    AttendanceStatus.ABSENT: (_("Out"), "pill-danger"),
    AttendanceStatus.MAYBE: (_("Maybe"), "pill-warn"),
    AttendanceStatus.NOT_SELECTED: (_("Not selected"), "pill-neutral"),
    AttendanceStatus.EXCUSED: (_("Excused"), "pill-neutral"),
    AttendanceStatus.NO_RESPONSE: (_("Reply"), "pill-warn"),
}


def rsvp_pill(status) -> tuple:
    return _RSVP_PILLS.get(status, (_("Reply"), "pill-warn"))


def signup_pill(kind, status) -> tuple:
    """(label, css class) of a referee/official sign-up: an accepted one is "Confirmed" in the
    role's own accent, an invite waits for a reply, a declined one says so."""
    if status == "accepted":
        return (_("Confirmed"), "pill-referee" if kind == "referee" else "pill-info")
    if status == "declined":
        return (_("Declined"), "pill-danger")
    return (_("Reply needed"), "pill-neutral")


# ---------------------------------------------------------------------------
# Calendar rows -> one group per event
# ---------------------------------------------------------------------------


def _time_range(event) -> str:
    start = timezone.localtime(event.start).strftime("%H:%M")
    if event.end is None:
        return start
    return f"{start}\u2013{timezone.localtime(event.end).strftime('%H:%M')}"


def _editable(row, event, now) -> bool:
    """Whether this person can still change their answer (the iOS ``isEditable``)."""
    if event.cancelled or event.start <= now:
        return False
    if row["type"] == "rsvp":
        attendance = row["attendance"]
        if attendance.status in (AttendanceStatus.SELECTED, AttendanceStatus.NOT_SELECTED):
            return False  # a published line-up supersedes the RSVP
        return event.deadline is None or event.deadline >= now
    return row.get("signup") is not None


def _answer(row):
    """True = In, False = Out, None = unanswered."""
    if row["type"] == "rsvp":
        status = row["attendance"].status
        if status in (AttendanceStatus.PRESENT, AttendanceStatus.SELECTED):
            return True
        if status == AttendanceStatus.ABSENT:
            return False
        return None
    status = row["signup"].status
    if status == RefereeSignup.Status.ACCEPTED:
        return True
    if status == RefereeSignup.Status.DECLINED:
        return False
    return None


def _line(row, event, me, now) -> dict:
    person = row["person"]
    if row["type"] == "rsvp":
        label, css = rsvp_pill(row["attendance"].status)
        role_label = _("Player")
        note = row["attendance"].note
    else:
        label, css = signup_pill(row["type"], row["signup"].status)
        role_label = _("Referee") if row["type"] == "referee" else _("Official")
        note = ""
    return {
        "type": row["type"],
        "person": person,
        "name": person_name(person, me),
        "role_label": role_label,
        "attendance": row.get("attendance"),
        "signup": row.get("signup"),
        "editable": _editable(row, event, now),
        "answer": _answer(row),
        "pill_label": label,
        "pill_class": css,
        "note": note,
        #: A referee/official line always names where it stands ("Reply needed"/"Confirmed");
        #: a player's own answer is already shown by the highlighted In/Out button.
        "show_pill": row["type"] != "rsvp" or not _editable(row, event, now),
    }


def _group_title(event, role) -> str:
    if not is_game_like(event):
        return event.title
    opponent = event.opponent.name if event.opponent_id else event.title
    kind_word = role or str(kind_style(event.kind)["label"])
    return str(_("%(role)s · %(opponent)s") % {"role": kind_word, "opponent": opponent})


def _group(event_rows, me, now, task_summary, show_names) -> dict:
    event = event_rows[0]["event"]
    style = kind_style(event.kind)
    types = {row["type"] for row in event_rows}
    game_like = is_game_like(event)

    # Being asked to ref/officiate a game reads very differently from being asked to play in it,
    # so a sign-up-only game gets its own role colour and wording (a player request always wins).
    role = None
    bar = style["bar"]
    if game_like and "rsvp" not in types:
        if "referee" in types:
            role, bar = str(_("Referee")), "bg-referee"
        elif "official" in types:
            role, bar = str(_("Official")), "bg-official"

    lines = [_line(row, event, me, now) for row in event_rows if row["type"] in ("rsvp", "referee", "official")]
    blocked = [{"person": row["person"], "name": person_name(row["person"], me), "requirements": row["requirements"]} for row in event_rows if row["type"] == "blocked"]
    multi_role = len({line["type"] for line in lines}) > 1
    finished = is_finished_game(event, now)

    team_names = [team.short_name or team.name for team in event.teams.all()]
    names = sorted({row["person"].first_name for row in event_rows})
    parts = []
    if game_like:
        parts.append(timezone.localtime(event.start).strftime("%H:%M"))
        if event.location_id:
            parts.append(str(_("Home")) if event.is_home_fixture else str(_("Away")))
        if team_names:
            parts.append(", ".join(team_names))
        if multi_role and names:
            parts.append(", ".join(names))
    else:
        parts.append(_time_range(event))
        if team_names:
            parts.append(", ".join(team_names))
        if show_names and names:
            parts.append(", ".join(names))

    open_tasks, total_tasks = task_summary
    return {
        "event": event,
        "rows": event_rows,
        "items": event_rows,
        "dom_id": f"event-{event.pk}",
        "style": style,
        "bar_class": bar,
        "title": _group_title(event, role),
        "meta": " · ".join(parts),
        "is_game_like": game_like,
        "finished": finished,
        "lines": lines if not finished else [],
        "blocked": blocked,
        "blocked_pill": (_("Blocked"), "pill-danger") if blocked and not lines else None,
        "multi_role": multi_role,
        "task_open": open_tasks if not finished else 0,
        "task_total": total_tasks,
        "anchor_today": False,
    }


def build_groups(rows, *, me, now=None, tasks=None, show_names=False) -> list:
    """``rows`` (start-sorted calendar rows, each with ``event``, ``type`` and ``person``) merged
    into one group per event, soonest first. Several people's rows on one event share a card."""
    now = now or timezone.now()
    tasks = tasks if tasks is not None else task_counts([row["event"].pk for row in rows])
    by_event = collections.OrderedDict()
    for row in rows:
        by_event.setdefault(row["event"].pk, []).append(row)
    order = {"rsvp": 0, "referee": 1, "official": 2, "blocked": 3}
    groups = []
    for event_rows in by_event.values():
        event_rows = sorted(event_rows, key=lambda row: (order.get(row["type"], 9), row["person"].first_name))
        groups.append(_group(event_rows, me, now, tasks.get(event_rows[0]["event"].pk, (0, 0)), show_names))
    return groups


def months_of(groups) -> list:
    """Groups bucketed per calendar month, oldest first: ``[{"month_start", "items"}]``."""
    result = []
    for month_start, items in itertools.groupby(groups, key=lambda group: timezone.localtime(group["event"].start).date().replace(day=1)):
        result.append({"month_start": month_start, "items": list(items)})
    return result


def mark_today_anchor(*group_lists, today) -> None:
    """Flags the group a "jump to today" should land on: the first event today, else the next one
    after it (the iOS ``scrollTargetGroupID``). The template puts ``id="today"`` on it."""
    for groups in group_lists:
        for group in groups:
            if timezone.localtime(group["event"].start).date() >= today:
                group["anchor_today"] = True
                return


# ---------------------------------------------------------------------------
# Filter menu
# ---------------------------------------------------------------------------


def kinds_present(rows) -> list:
    """The kinds in ``rows`` as ``[{"value", "label"}]`` in the menu's fixed order, plus any
    unknown kind after them."""
    present = {row["event"].kind for row in rows}
    known = [kind for kind in KIND_ORDER if kind in present]
    unknown = sorted(present - set(KIND_ORDER))
    return [{"value": kind, "label": kind_style(kind)["filter_label"]} for kind in known] + [{"value": kind, "label": str(kind).capitalize()} for kind in unknown]


def apply_filters(rows, *, kinds=None, referee=True, official=True) -> list:
    """Rows without the kinds the filter menu hid (``kinds`` None means all) or the referee /
    official requests it switched off."""
    result = []
    for row in rows:
        if kinds is not None and row["event"].kind not in kinds:
            continue
        if row["type"] == "referee" and not referee:
            continue
        if row["type"] == "official" and not official:
            continue
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Month grid
# ---------------------------------------------------------------------------


def parse_month(value, fallback: datetime.date) -> datetime.date:
    """``YYYY-MM`` -> the first of that month, else ``fallback``'s month."""
    try:
        year, month = (int(part) for part in str(value).split("-"))
        return datetime.date(year, month, 1)
    except TypeError, ValueError:
        return fallback.replace(day=1)


def parse_day(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value))
    except TypeError, ValueError:
        return None


def month_window(month_start: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """The aware datetimes spanning the whole month grid (full weeks, so the leading/trailing
    days of the neighbouring months are covered too)."""
    first_day, last_day = month_bounds(month_start)
    grid_start = first_day - datetime.timedelta(days=first_day.weekday())
    grid_end = last_day + datetime.timedelta(days=6 - last_day.weekday())
    zone = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.datetime.combine(grid_start, datetime.time.min), zone),
        timezone.make_aware(datetime.datetime.combine(grid_end + datetime.timedelta(days=1), datetime.time.min), zone),
    )


def month_pane(groups, *, month_start: datetime.date, selected: datetime.date, today: datetime.date, url_for) -> dict:
    """The server-rendered month grid plus the selected day's groups. ``url_for(month=, day=)``
    builds the link for a day cell / month arrow."""
    by_day = collections.defaultdict(list)
    for group in groups:
        by_day[timezone.localtime(group["event"].start).date()].append(group)

    grid = month_grid([], month_start)
    weeks = []
    for week in grid["weeks"]:
        cells = []
        for cell in week:
            day = cell["date"]
            cells.append(
                {
                    "date": day,
                    "in_month": cell["in_month"],
                    "is_today": day == today,
                    "selected": day == selected,
                    "has_events": bool(by_day.get(day)),
                    "url": url_for(month=day.strftime("%Y-%m"), day=day.isoformat()),
                }
            )
        weeks.append(cells)

    previous_month = (month_start - datetime.timedelta(days=1)).replace(day=1)
    next_month = (month_start + datetime.timedelta(days=32)).replace(day=1)
    return {
        "month_start": month_start,
        "weeks": weeks,
        "selected": selected,
        "selected_groups": by_day.get(selected, []),
        "prev_url": url_for(month=previous_month.strftime("%Y-%m"), day=None),
        "next_url": url_for(month=next_month.strftime("%Y-%m"), day=None),
        "is_current_month": month_start == today.replace(day=1),
        "today_url": url_for(month=None, day=None),
        # Monday-first headers (events.services.calendar.month_grid's own week start).
        "weekday_labels": [datetime.date(2024, 1, 1 + index) for index in range(7)],
    }


# ---------------------------------------------------------------------------
# Event sheet
# ---------------------------------------------------------------------------

_IOS_MARKERS = ("iPhone", "iPad", "iPod")


def maps_url(location, user_agent: str = "") -> str | None:
    """A plain link-out to the location in the user's maps app (Apple Maps on iOS, Google Maps
    elsewhere) -- no embedded map and no geocoding provider. ``None`` without usable location data."""
    if location is None:
        return None
    place = " ".join(part for part in (location.zip_code, location.city) if part)
    query = ", ".join(part for part in (location.name, location.address, place) if part)
    if not (location.address or location.name):
        return None
    if any(marker in (user_agent or "") for marker in _IOS_MARKERS):
        return f"https://maps.apple.com/?q={quote(query)}"
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def hero_for(event, *, rsvp_rows_present: bool, referee_signups, official_signups) -> dict:
    """The sheet's hero: eyebrow word + colour class, and "Team vs Opponent" for a game."""
    style = kind_style(event.kind)
    game_like = is_game_like(event)
    badge = style["badge"]
    if game_like and not rsvp_rows_present and referee_signups:
        eyebrow, badge = _("Referee"), "bg-referee text-white"
    elif game_like and not rsvp_rows_present and official_signups:
        eyebrow, badge = _("Official"), "bg-official text-white"
    elif event.kind == EventKind.GAME:
        eyebrow = _("Home game") if event.is_home_game else _("Away game")
    else:
        eyebrow = style["label"]

    title = event.title
    if game_like and event.opponent_id:
        opponent = event.opponent.name
        team = ", ".join(team.short_name or team.name for team in event.teams.all())
        home = event.is_home_fixture
        if not team:
            title = _("vs %(opponent)s") % {"opponent": opponent} if home else _("Away · %(opponent)s") % {"opponent": opponent}
        elif home:
            title = _("%(home)s vs %(away)s") % {"home": team, "away": opponent}
        else:
            title = _("%(home)s vs %(away)s") % {"home": opponent, "away": team}
    return {"eyebrow": eyebrow, "badge": badge, "title": str(title)}


def shape_answers(view: dict, event) -> list:
    """The member view's per-person answers, each with the flags the sheet needs: ``locked`` (no
    In/Maybe/Out any more), ``can_drop_out`` (selected in a published line-up) and
    ``deadline_passed``."""
    locked_by_event = view["rsvp_closed"] or view["started"] or view["lineup"] is not None
    answers = []
    for answer in view["your_answers"]:
        status = answer["attendance"].status
        can_drop_out = view["lineup"] is not None and status == AttendanceStatus.SELECTED and not view["started"]
        answers.append({**answer, "locked": locked_by_event, "can_drop_out": can_drop_out, "deadline_passed": view["rsvp_closed"] and not can_drop_out})
    return answers
