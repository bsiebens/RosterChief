"""Creating, editing and deleting events and recurring series (the calendar API calls these).

Permissions: creating needs ``can_schedule`` for the new audience (settings holders always; otherwise only teams you
manage and/or groups you are in, never club-wide). Editing or deleting also needs ``can_manage_event`` for the existing
event, and for a series-wide change the same rule for the series' own audience. Failures raise ``NotAllowed`` (403);
broken input raises ``Invalid`` (422) with field errors keyed by the value names below.

Values (keyword arguments; every key optional on update = PATCH semantics, missing keys keep their value)::

    title: str                      required, except: a game's title is always generated ("U12 vs Rivals", home side
                                    first), and a training / tournament without a title is called after its kind
    kind: str                       Event.EventKind value, default "other"
    start: datetime                 required (aware; naive is read as local time)
    end, gathering, deadline: datetime | None
                                    end > start, gathering <= start, deadline <= start. When only ``start`` changes on
                                    an update, the others move along with it.
    location_id, opponent_id: UUID | str | None
    team_ids, group_ids, invited_member_ids, excluded_member_ids: list[UUID | str]
    club_wide: bool                 cannot be combined with teams or groups
    # events only
    season_id: UUID | str | None    derived from the start date when omitted / None
    is_friendly: bool
    cancelled: bool                 keeps the row but marks it cancelled
    # series only
    recurrence: {"frequency": "daily" | "weekly" | "monthly", "interval": int >= 1, "weekdays": ["MO", ...] (weekly,
                 at least one), "until": date | None, "count": int | None (not both)}
    rrule: str                      advanced: a raw RFC 5545 RRULE, wins over ``recurrence``

A game needs at least one team (old rule). Fields not handled here on purpose: referees / officials, tasks,
attachments, competition, external ids, scores and live state.

Scopes for an occurrence of a series (``update_event`` / ``delete_event``; one-off events ignore the scope)::

    "this"       update: only this row changes and it is detached from the series (series-wide edits skip it from
                 then on; a moved start leaves an EXDATE so the slot is not regenerated).
                 delete: EXDATE + the row is removed.
    "following"  update: the series is split at this occurrence; the new series (this one and every later occurrence)
                 gets the new values, the original ends just before it.
                 delete: the series ends just before this occurrence and this and every later occurrence is removed
                 (the series itself goes when nothing is left).
    "all"        update: the series template changes and is re-applied to every non-detached occurrence from now on,
                 plus this occurrence itself (which is re-attached). Past occurrences keep their values.
                 delete: the series and its future occurrences are removed; past occurrences are kept as one-off
                 events.
    For "following" / "all" the start may only change its time of day (rows are moved in place, attendance kept); to
    move the series to other days, change its recurrence with ``update_series``.

Audience m2m fields are written with ``.set()`` so the attendance-sync signals run by themselves.
Audit actions: event.created / event.updated / event.deleted / series.created / series.updated / series.deleted /
series.stopped (club level, target = the event or series).
"""

import datetime
import uuid

from django.db import transaction
from django.utils import timezone

from events.models import Event, EventSeries, Location, Opponent, Season
from members.models import Group, Member
from members.services import audit
from members.services.access import can_manage_any_team, can_manage_event, can_schedule, user_group_ids
from members.services.errors import Invalid, NotAllowed
from teams.models import Team

from . import recurrence as rec

MSG = "Please correct the highlighted fields."
SCOPES = ("this", "following", "all")

COMMON_KEYS = (
    "title", "kind", "start", "end", "gathering", "deadline", "location_id", "opponent_id",
    "team_ids", "group_ids", "club_wide", "invited_member_ids", "excluded_member_ids",
)  # fmt: skip
EVENT_KEYS = (*COMMON_KEYS, "season_id", "is_friendly", "cancelled")
SERIES_KEYS = (*COMMON_KEYS, "recurrence", "rrule")
M2M = {"team_ids": "teams", "group_ids": "groups", "invited_member_ids": "invited_members", "excluded_member_ids": "excluded_members"}
DEFAULTS = {
    "title": "", "kind": Event.EventKind.OTHER, "start": None, "end": None, "gathering": None, "deadline": None,
    "location_id": None, "opponent_id": None, "team_ids": [], "group_ids": [], "club_wide": False,
    "invited_member_ids": [], "excluded_member_ids": [], "season_id": None, "is_friendly": False, "cancelled": False,
}  # fmt: skip


# -- permissions --


def _require_any_scheduling(actor) -> None:
    if not (can_manage_any_team(actor) or user_group_ids(actor)):
        raise NotAllowed("You are not allowed to schedule events.")


def _require_schedule(actor, cleaned) -> None:
    if not can_schedule(actor, team_ids=[t.pk for t in cleaned["teams"]], group_ids=[g.pk for g in cleaned["groups"]], club_wide=cleaned["club_wide"]):
        raise NotAllowed("You can only schedule events for teams you manage and groups you are in; club-wide events need the settings permission.")


def _require_manage(actor, event) -> None:
    if not can_manage_event(actor, event):
        raise NotAllowed("You are not allowed to change this event.")


def can_manage_series(actor, series) -> bool:
    return can_schedule(actor, team_ids=series.teams.values_list("pk", flat=True), group_ids=series.groups.values_list("pk", flat=True), club_wide=series.club_wide)


def _require_series(actor, series) -> None:
    if not can_manage_series(actor, series):
        raise NotAllowed("You are not allowed to change this series.")


# -- validation --


def _check_keys(values, allowed) -> None:
    unknown = set(values) - set(allowed)
    if unknown:
        raise TypeError(f"Unknown event value(s): {', '.join(sorted(unknown))}")


def _aware(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except TypeError, ValueError, AttributeError:
        return None


def _one(model, value, field, errors, message):
    if value in (None, ""):
        return None
    pk = _uuid(value)
    obj = model.objects.filter(pk=pk).first() if pk else None
    if obj is None:
        errors[field] = [message]
    return obj


def _many(model, values, field, errors, message):
    ids = []
    for value in values or []:
        pk = _uuid(value)
        if pk is None:
            errors[field] = [message]
            return []
        if pk not in ids:
            ids.append(pk)
    found = {o.pk: o for o in model.objects.filter(pk__in=ids)}
    if len(found) != len(ids):
        errors[field] = [message]
        return []
    return [found[pk] for pk in ids]


def _auto_title(kind, title, teams, opponent, location, *, creating):
    """Port of the old EventForm title rules: a game is always named after its match-up (home side first; just our
    teams, the opponent or "Game" while a side is missing); a new training / tournament without a title is called
    after its kind."""
    if kind == Event.EventKind.GAME:
        us = ", ".join(t.short_name or t.name for t in teams)
        if us and opponent:
            is_away = location is not None and not location.is_home
            home, away = (opponent.name, us) if is_away else (us, opponent.name)
            return f"{home} vs {away}"
        return us or (opponent.name if opponent else str(Event.EventKind.GAME.label))
    if not title and creating and kind in (Event.EventKind.TRAINING, Event.EventKind.TOURNAMENT):
        return str(Event.EventKind(kind).label)
    return title


def _clean(values: dict, errors: dict, *, creating: bool) -> dict:
    """Validated, resolved values (model instances instead of ids). Adds to ``errors``; the caller raises."""
    out = {}
    kind = values.get("kind") or Event.EventKind.OTHER
    if kind not in Event.EventKind.values:
        errors["kind"] = ["Choose a valid kind."]
        kind = Event.EventKind.OTHER
    out["kind"] = kind
    for name in ("start", "end", "gathering", "deadline"):
        try:
            out[name] = _aware(values.get(name))
        except TypeError, ValueError:
            errors[name] = ["Enter a valid date and time."]
            out[name] = None
    start = out["start"]
    if start is None and "start" not in errors:
        errors["start"] = ["This field is required."]
    if start is not None:
        if out["end"] is not None and out["end"] <= start:
            errors["end"] = ["The end must be after the start."]
        if out["gathering"] is not None and out["gathering"] > start:
            errors["gathering"] = ["The gathering time can't be after the start."]
        if out["deadline"] is not None and out["deadline"] > start:
            errors["deadline"] = ["The registration deadline can't be after the start."]

    out["location"] = _one(Location, values.get("location_id"), "location_id", errors, "Unknown location.")
    out["opponent"] = _one(Opponent, values.get("opponent_id"), "opponent_id", errors, "Unknown opponent.")
    out["teams"] = _many(Team, values.get("team_ids"), "team_ids", errors, "Unknown team.")
    out["groups"] = _many(Group, values.get("group_ids"), "group_ids", errors, "Unknown group.")
    out["invited_members"] = _many(Member, values.get("invited_member_ids"), "invited_member_ids", errors, "Unknown member.")
    out["excluded_members"] = _many(Member, values.get("excluded_member_ids"), "excluded_member_ids", errors, "Unknown member.")
    out["club_wide"] = bool(values.get("club_wide"))
    if out["club_wide"] and (out["teams"] or out["groups"]):
        errors["club_wide"] = ["A whole-club event can't also list specific teams or groups: clear them, or turn this off."]
    if kind == Event.EventKind.GAME and not out["teams"] and "team_ids" not in errors:
        errors["team_ids"] = ["A game needs at least one team."]

    title = _auto_title(kind, (values.get("title") or "").strip(), out["teams"], out["opponent"], out["location"], creating=creating)
    if not title:
        errors["title"] = ["Give this event a title."]
    out["title"] = title[:255]

    season = None
    if values.get("season_id") not in (None, ""):
        season = _one(Season, values.get("season_id"), "season_id", errors, "Unknown season.")
    elif start is not None:
        season = Season.get_current(timezone.localtime(start).date())
    out["season"] = season
    out["is_friendly"] = bool(values.get("is_friendly"))
    out["cancelled"] = bool(values.get("cancelled"))
    return out


def _clean_recurrence(values: dict, start, errors: dict) -> tuple[str, datetime.datetime | None]:
    """(rrule, until) from ``rrule`` (raw, wins) or the friendly ``recurrence`` dict."""
    raw = (values.get("rrule") or "").strip()
    spec = values.get("recurrence") or {}
    until = None
    if raw:
        if raw.upper().startswith("RRULE:"):
            raw = raw[6:]
        if "\n" in raw or "DTSTART" in raw.upper() or "FREQ=" not in raw.upper() or len(raw) > 255:
            errors["rrule"] = ["Enter a single RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,WE."]
            return raw, None
        rule = raw
    else:
        frequency = spec.get("frequency")
        if frequency not in rec.FREQUENCIES:
            errors["frequency"] = ["Choose daily, weekly or monthly."]
            return "", None
        interval = spec.get("interval")
        interval = 1 if interval is None else interval
        if not isinstance(interval, int) or not 1 <= interval <= 99:
            errors["interval"] = ["Must be between 1 and 99."]
        weekdays = list(spec.get("weekdays") or [])
        if frequency == "weekly":
            if not weekdays:
                errors["weekdays"] = ["Pick at least one day of the week."]
            elif any(code not in rec.WEEKDAY_CODES for code in weekdays):
                errors["weekdays"] = ["Unknown day of the week."]
        count = spec.get("count")
        if count is not None and (not isinstance(count, int) or not 1 <= count <= 1000):
            errors["count"] = ["Must be between 1 and 1000."]
        until_day = spec.get("until")
        if count and until_day:
            errors["until"] = ["Give an end date or a number of times, not both."]
        if until_day:
            if isinstance(until_day, str):
                try:
                    until_day = datetime.date.fromisoformat(until_day)
                except ValueError:
                    errors["until"] = ["Enter a valid date."]
                    until_day = None
            elif isinstance(until_day, datetime.datetime):
                until_day = until_day.date()
            if until_day is not None:
                if start is not None and until_day < timezone.localtime(start).date():
                    errors["until"] = ["The end date can't be before the first occurrence."]
                until = rec.end_of_day(until_day)
        if set(errors) & {"interval", "weekdays", "count"}:
            return "", until
        rule = rec.build_rrule(frequency, interval, weekdays, count)
    if start is not None:
        try:
            r = rec.make_rule(rule, start)
            r.after(timezone.localtime(start), inc=True)
        except (ValueError, TypeError) as e:
            errors["rrule" if raw else "frequency"] = [f"This recurrence rule is not valid: {e}"]
    return rule, until


def _raise(errors):
    if errors:
        raise Invalid(MSG, errors)


# -- reading back (for edit forms and audit) --


def _ids(objs):
    return [o.pk for o in objs]


def event_values(event: Event) -> dict:
    """The editable values of ``event`` in the input shape (ids as UUIDs)."""
    return {
        "title": event.title, "kind": event.kind, "start": event.start, "end": event.end, "gathering": event.gathering,
        "deadline": event.deadline, "location_id": event.location_id, "opponent_id": event.opponent_id,
        "team_ids": _ids(event.teams.all()), "group_ids": _ids(event.groups.all()), "club_wide": event.club_wide,
        "invited_member_ids": _ids(event.invited_members.all()), "excluded_member_ids": _ids(event.excluded_members.all()),
        "is_friendly": event.is_friendly, "cancelled": event.cancelled,
    }  # fmt: skip


def series_values(series: EventSeries) -> dict:
    """The editable values of ``series`` in the input shape: the first occurrence's times plus
    ``recurrence`` = recurrence.recurrence_values(series) (``friendly`` False means: edit ``rrule`` as raw text)."""
    start = series.dtstart
    return {
        "title": series.title, "kind": series.kind, "start": start,
        "end": start + series.duration if series.duration is not None else None,
        "gathering": start - series.gathering_offset if series.gathering_offset is not None else None,
        "deadline": start - series.deadline_offset if series.deadline_offset is not None else None,
        "location_id": series.location_id, "opponent_id": series.opponent_id,
        "team_ids": _ids(series.teams.all()), "group_ids": _ids(series.groups.all()), "club_wide": series.club_wide,
        "invited_member_ids": _ids(series.invited_members.all()), "excluded_member_ids": _ids(series.excluded_members.all()),
        "recurrence": rec.recurrence_values(series),
    }  # fmt: skip


def _state(obj) -> dict:
    fields = ["title", "kind", "location_id", "opponent_id", "club_wide"]
    if isinstance(obj, Event):
        fields += ["start", "end", "gathering", "deadline", "season_id", "is_friendly", "cancelled", "detached", "series_id"]
    else:
        fields += ["rrule", "dtstart", "until", "duration", "gathering_offset", "deadline_offset"]
    out = {f: getattr(obj, f) for f in fields}
    for key, rel in M2M.items():
        out[key] = sorted(str(pk) for pk in getattr(obj, rel).values_list("pk", flat=True))
    return out


def _changes(before: dict, after: dict) -> dict:
    return {k: {"from": audit._json(before[k]), "to": audit._json(after[k])} for k in after if audit._json(before.get(k)) != audit._json(after[k])}


def _created(state: dict) -> dict:
    return {k: {"from": None, "to": audit._json(v)} for k, v in state.items()}


def _merge(current: dict, values: dict) -> dict:
    """``current`` overlaid with ``values``; when only the start moves, end / gathering / deadline move with it."""
    merged = {**current, **values}
    old_start, new_start = current.get("start"), values.get("start")
    if "start" in values and old_start is not None and new_start is not None:
        try:
            delta = _aware(new_start) - old_start
        except TypeError, ValueError:
            return merged
        for key in ("end", "gathering", "deadline"):
            if key not in values and current.get(key) is not None:
                merged[key] = current[key] + delta
    return merged


# -- writing --


def _member_of(actor):
    if actor is None or not getattr(actor, "pk", None):
        return None
    return Member.objects.filter(user=actor).first()


def _assign_event(event: Event, c: dict) -> None:
    for f in ("title", "kind", "start", "end", "gathering", "deadline", "location", "opponent", "season", "club_wide", "is_friendly", "cancelled"):
        setattr(event, f, c[f])
    event.save()
    for rel in M2M.values():
        getattr(event, rel).set(c[rel])


def _assign_template(series: EventSeries, c: dict) -> None:
    start = c["start"]
    series.title, series.kind, series.location, series.opponent, series.club_wide = c["title"], c["kind"], c["location"], c["opponent"], c["club_wide"]
    series.duration = c["end"] - start if c["end"] is not None else None
    series.gathering_offset = start - c["gathering"] if c["gathering"] is not None else None
    series.deadline_offset = start - c["deadline"] if c["deadline"] is not None else None
    series.save()
    for rel in M2M.values():
        getattr(series, rel).set(c[rel])


@transaction.atomic
def create_event(actor, **values) -> Event:
    """A one-off event."""
    _check_keys(values, EVENT_KEYS)
    _require_any_scheduling(actor)
    errors = {}
    c = _clean({**DEFAULTS, **values}, errors, creating=True)
    _raise(errors)
    _require_schedule(actor, c)
    event = Event(created_by=_member_of(actor))
    _assign_event(event, c)
    audit.record(actor, "event.created", target=event, changes=_created(_state(event)))
    return event


@transaction.atomic
def update_event(actor, event: Event, scope: str = "this", **values) -> Event:
    """Edit a one-off event, or an occurrence of a series with ``scope`` (see the module docstring). Returns the
    edited event (re-read: for "following" it now belongs to the new series)."""
    _check_keys(values, EVENT_KEYS)
    if scope not in SCOPES:
        raise Invalid(MSG, {"scope": ["Choose this, following or all."]})
    _require_manage(actor, event)
    current = event_values(event)
    merged = _merge(current, values)
    errors = {}
    c = _clean(merged, errors, creating=False)
    series = event.series if event.series_id else None
    if series is not None and scope != "this":
        for key in ("is_friendly", "cancelled"):
            if key in values and bool(values[key]) != current[key]:
                errors[key] = ["This can only be changed for a single occurrence."]
        if c["start"] is not None and timezone.localtime(c["start"]).date() != timezone.localtime(event.start).date():
            errors["start"] = ["To move the series to other days, change its recurrence."]
    _raise(errors)
    _require_schedule(actor, c)

    if series is None or scope == "this":
        before = _state(event)
        old_start = event.start
        _assign_event(event, c)
        if series is not None:
            if event.start != old_start:
                rec.exclude_date(series, old_start)  # the slot is taken by this detached row
            rec.detach_occurrence(event)
        changes = _changes(before, _state(event))
        if changes:
            audit.record(actor, "event.updated", target=event, changes={**changes, "scope": {"from": None, "to": "this" if series else "event"}})
        return event

    _require_series(actor, series)
    now = timezone.now()
    first = series.occurrences.order_by("start").values_list("pk", flat=True).first()
    if scope == "following" and first == event.pk:
        scope, since = "all", min(now, event.start)
    elif scope == "following":
        since = event.start
    else:
        since = now
    target = rec.split_series(series, event.start) if scope == "following" else series
    before = _state(target)
    _assign_template(target, c)
    new_time = timezone.localtime(c["start"]).time()
    if new_time != timezone.localtime(target.dtstart).time():
        rec.shift_time_of_day(target, new_time, since=since)
    updated = {e.pk for e in rec.propagate_series(target, since=since)}
    event.refresh_from_db()
    if event.pk not in updated:  # the edited occurrence itself: past or detached, re-attach it
        if timezone.localtime(event.start).time() != new_time.replace(tzinfo=None):
            event.start = timezone.make_aware(datetime.datetime.combine(timezone.localtime(event.start).date(), new_time.replace(tzinfo=None)))
        event.detached = False
        rec.apply_template(target, event)
    rec.generate_occurrences(target)
    target.refresh_from_db()
    changes = _changes(before, _state(target))
    audit.record(actor, "series.updated", target=target, changes={**changes, "scope": {"from": None, "to": scope}, "from_event": {"from": None, "to": str(event.pk)}})
    event.refresh_from_db()
    return event


@transaction.atomic
def delete_event(actor, event: Event, scope: str = "this") -> None:
    """Delete a one-off event, or an occurrence of a series with ``scope`` (see the module docstring)."""
    if scope not in SCOPES:
        raise Invalid(MSG, {"scope": ["Choose this, following or all."]})
    _require_manage(actor, event)
    series = event.series if event.series_id else None
    if series is None or scope == "this":
        snap, pk, label = _state(event), event.pk, str(event)
        if series is not None:
            rec.cancel_occurrence(event)
        else:
            event.delete()
        _record_deleted(actor, "event.deleted", "event", pk, label, {**{k: {"from": audit._json(v), "to": None} for k, v in snap.items()}, "scope": {"from": None, "to": scope if series else "event"}})
        return
    _require_series(actor, series)
    if scope == "all":
        _delete_series(actor, series)
        return
    at = event.start
    removed = Event.objects.filter(series=series, start__gte=at).delete()[1].get("events.Event", 0)
    if not series.occurrences.exists():
        _delete_series(actor, series)
        return
    series.until = at - datetime.timedelta(microseconds=1)
    series.excluded_dates = [v for v in series.excluded_dates if datetime.datetime.fromisoformat(v) < at]
    series.save(update_fields=["until", "excluded_dates", "modified"])
    audit.record(actor, "series.updated", target=series, changes={"until": {"from": None, "to": audit._json(series.until)}, "removed_occurrences": {"from": None, "to": removed}, "scope": {"from": None, "to": "following"}})


def _record_deleted(actor, action, model_name, pk, label, changes):
    entry = audit.record(actor, action, changes=changes)
    entry.target_type, entry.target_id, entry.target_label = model_name, str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


def _delete_series(actor, series: EventSeries) -> None:
    now = timezone.now()
    kept = Event.objects.filter(series=series, start__lt=now).update(series=None, detached=False)
    snap, pk, label = _state(series), series.pk, str(series)
    removed = series.occurrences.count()
    series.delete()  # cascades to the future occurrences
    changes = {**{k: {"from": audit._json(v), "to": None} for k, v in snap.items()}, "removed_occurrences": {"from": None, "to": removed}, "kept_past_occurrences": {"from": None, "to": kept}}
    _record_deleted(actor, "series.deleted", "eventseries", pk, label, changes)


@transaction.atomic
def create_series(actor, **values) -> EventSeries:
    """A recurring series; its occurrences up to the horizon are created straight away."""
    _check_keys(values, SERIES_KEYS)
    _require_any_scheduling(actor)
    errors = {}
    merged = {**{k: v for k, v in DEFAULTS.items() if k in SERIES_KEYS}, **values}
    c = _clean(merged, errors, creating=True)
    if not values.get("rrule") and not values.get("recurrence"):
        errors["frequency"] = ["Choose how often this repeats."]
        rrule, until = "", None
    else:
        rrule, until = _clean_recurrence(values, c["start"], errors)
    _raise(errors)
    _require_schedule(actor, c)
    series = EventSeries(rrule=rrule, dtstart=c["start"], until=until)
    _assign_template(series, c)
    rec.generate_occurrences(series)
    audit.record(actor, "series.created", target=series, changes=_created(_state(series)))
    return series


@transaction.atomic
def update_series(actor, series: EventSeries, **values) -> EventSeries:
    """Edit the series itself (template and/or recurrence). Template changes are re-applied to the non-detached
    occurrences from now on; a time-of-day change moves them in place; a recurrence change (rule, first date, end)
    regenerates: future non-detached occurrences the new rule no longer produces are removed, new ones created.
    Past and detached occurrences are never touched."""
    _check_keys(values, SERIES_KEYS)
    _require_series(actor, series)
    current = series_values(series)
    current.pop("recurrence")
    merged = _merge(current, {k: v for k, v in values.items() if k not in ("recurrence", "rrule")})
    errors = {}
    c = _clean(merged, errors, creating=False)
    if "recurrence" in values or "rrule" in values:
        rrule, until = _clean_recurrence(values, c["start"], errors)
    else:
        rrule, until = series.rrule, series.until
        if c["start"] is not None:
            try:
                rec.make_rule(rrule, c["start"])
            except ValueError as e:
                errors["rrule"] = [f"This recurrence rule is not valid: {e}"]
    _raise(errors)
    _require_schedule(actor, c)

    before = _state(series)
    now = timezone.now()
    old_start = timezone.localtime(series.dtstart)
    new_start = timezone.localtime(c["start"])
    rule_changed = rrule != series.rrule or until != series.until or new_start.date() != old_start.date()
    _assign_template(series, c)
    if rule_changed:
        series.rrule, series.until, series.dtstart = rrule, until, c["start"]
        series.save(update_fields=["rrule", "until", "dtstart", "modified"])
        rec.regenerate_series(series, now=now)
    elif new_start.time() != old_start.time():
        rec.shift_time_of_day(series, new_start.time(), since=now)
    rec.propagate_series(series, since=now)
    rec.generate_occurrences(series)
    series.refresh_from_db()
    changes = _changes(before, _state(series))
    if changes:
        audit.record(actor, "series.updated", target=series, changes={**changes, "scope": {"from": None, "to": "series"}})
    return series


@transaction.atomic
def delete_series(actor, series: EventSeries) -> None:
    """Remove the series and its future occurrences; past occurrences stay as one-off events."""
    _require_series(actor, series)
    _delete_series(actor, series)


@transaction.atomic
def stop_series(actor, series: EventSeries) -> EventSeries:
    """Stop generating new occurrences (``until`` = now); existing occurrences are untouched (old "Stop repeating")."""
    _require_series(actor, series)
    old = series.until
    series.until = timezone.now()
    series.save(update_fields=["until", "modified"])
    audit.record(actor, "series.stopped", target=series, changes={"until": {"from": audit._json(old), "to": audit._json(series.until)}})
    return series
