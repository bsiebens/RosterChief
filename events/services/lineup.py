"""A game's selection ("line-up"): a yes/no pick per player (``LineupSelection``), optionally placed in a slot ("line").

Slots exist only when the club's sport has them switched on by a platform admin (``Sport.lineup_slots_enabled`` with a
``lineup_template``); a slot is stored as ``"<group key>.<slot key>"`` (``LineupSlotAssignment``): one player per slot, a player may hold several slots (a line plus power play ...), only picked players. They are
informational only: they never change attendance and are never used for statistics. Unknown slot values (template edited,
feature switched off later) are ignored when reading (``selection_slots``).

Ported from the old app's ``events.services.lineup`` (and the rules its coach screens / app API enforced):

* Only games and tournaments have a line-up (``LINEUP_EVENT_KINDS``). One line-up per event, for one of its teams.
* The pickable players are the team's roster for the event's season whose answer is *available*: anything but
  absent, excused or no response (``UNAVAILABLE_STATUSES``). A roster player without an attendance row counts as
  "no response". Un-picking is always allowed (also for someone who dropped out or left the roster).
* Saving picks (``toggle_selection`` / ``set_selection``) never changes an answer and never notifies anyone. Only
  ``publish_lineup`` does: selected players become SELECTED, every other available invitee NOT_SELECTED, unavailable
  answers stay as they are. It doubles as "publish changes": only the people whose situation changed since the last
  publish are told (``changes_on_publish``).
* The first publish (or a schedule) of an empty line-up is refused. ``schedule_publish`` publishes it automatically
  later (``events.tasks.publish_scheduled_lineups``), ``unschedule`` cancels that.

Every write needs ``members.services.access.can_manage_attendance`` (settings holders, or a manager of one of the
event's teams). Audit actions: ``lineup.created``, ``lineup.published``, ``lineup.scheduled``, ``lineup.unscheduled``
(target = the line-up; ``actor`` None when the scheduled sweep published it)."""

from django.db import connection, transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, get_tenant_model

from club.lineup_template import slot_keys
from events.models import Attendance, Event, Lineup, LineupSelection, LineupSlotAssignment, Season
from members.models import Member
from members.services import audit
from members.services.access import can_manage_attendance
from members.services.errors import Invalid, NotAllowed
from notifications import inbox
from notifications.models import Category
from teams.models import StaffAssignment, TeamMembership

#: Answers that mean "not actually available": not pickable, and left untouched on publish.
UNAVAILABLE_STATUSES = [Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.EXCUSED, Attendance.AttendanceStatus.NO_RESPONSE]
LINEUP_EVENT_KINDS = [Event.EventKind.GAME, Event.EventKind.TOURNAMENT]


def _fail(field, message):
    raise Invalid(message, {field: [message]})


def require_lineup_manager(actor, event) -> None:
    if not can_manage_attendance(actor, event):
        raise NotAllowed("You are not allowed to manage the line-up of this event.")


def event_season(event) -> Season | None:
    """The season an event belongs to (explicit, else the one covering its start)."""
    return event.season or Season.covering(None, timezone.localtime(event.start).date())


def has_lineup(event) -> bool:
    return event.kind in LINEUP_EVENT_KINDS


def current_club():
    """The club whose schema is active, or None in the public schema. Works inside ``schema_context`` too (where
    ``connection.tenant`` is only a stand-in carrying the schema name)."""
    schema = connection.schema_name
    if not schema or schema == get_public_schema_name():
        return None
    tenant = getattr(connection, "tenant", None)
    model = get_tenant_model()
    if isinstance(tenant, model) and tenant.pk:
        return tenant
    return model.objects.filter(schema_name=schema).first()


def _notify_members(members, *, title, body, kind, href, category, club=None) -> list:
    """Inbox (+ push) for every member with a login in the current club; returns the users told."""
    club = club or current_club()
    if club is None:
        return []
    told = []
    from members.services import recipients

    for member in members:
        told.extend(recipients.deliver(member, club, title=title, body=body, kind=kind, href=href, push=True, category=category))
    return told


def _href(event) -> str:
    return f"/calendar/events/{event.pk}"


# --- the line-up itself -----------------------------------------------------------------------------------------------


@transaction.atomic
def get_or_create_lineup(actor, event, team=None) -> Lineup:
    """The event's line-up, created for ``team`` (one of the event's teams; may be left out when the event has exactly
    one team) on first use. Games and tournaments only."""
    require_lineup_manager(actor, event)
    if not has_lineup(event):
        _fail("event", "Only games and tournaments have a line-up.")
    teams = list(event.teams.all())
    if team is None:
        if len(teams) != 1:
            _fail("team_id", "Pick the team this line-up is for.")
        team = teams[0]
    if team.pk not in {t.pk for t in teams}:
        _fail("team_id", "This team does not play in this event.")
    lineup = Lineup.objects.select_for_update().filter(event=event).first()
    if lineup is not None:
        if lineup.team_id != team.pk:
            _fail("team_id", "This event already has a line-up for another team.")
        return lineup
    lineup = Lineup.objects.create(event=event, team=team, created_by=_actor_member(actor))
    audit.record(actor, "lineup.created", target=lineup, changes=audit.snapshot(lineup, ["event", "team"]))
    return lineup


def _actor_member(actor):
    if actor is None or not getattr(actor, "pk", None):
        return None
    return Member.objects.filter(user=actor).first()


def _roster(lineup) -> dict:
    """``{member_id: TeamMembership}`` of the line-up's team in the event's season."""
    season = event_season(lineup.event)
    if season is None:
        return {}
    return {tm.member_id: tm for tm in TeamMembership.objects.filter(team=lineup.team, season=season).select_related("member", "position")}


def _statuses(event) -> dict:
    return dict(Attendance.objects.filter(event=event).values_list("member_id", "status"))


def _selected_ids(lineup) -> set:
    return set(LineupSelection.objects.filter(lineup=lineup).values_list("member_id", flat=True))


def selectable_members(lineup) -> list[dict]:
    """Every roster player of the line-up's team (event's season), plus anyone still picked who is no longer on it,
    with what the selection screen shows: ``member``, ``position`` (name or ""), ``position_order``, ``jersey_number``,
    ``status`` (attendance answer; "no_response" without a row), ``available`` (pickable), ``on_roster``, ``selected``.
    Sorted by position, then name."""
    roster = _roster(lineup)
    statuses = _statuses(lineup.event)
    selected = _selected_ids(lineup)
    members = {member_id: tm.member for member_id, tm in roster.items()}
    for member in Member.objects.filter(pk__in=selected - set(members)):
        members[member.pk] = member
    rows = []
    for member_id, member in members.items():
        tm = roster.get(member_id)
        position = tm.position if tm is not None and tm.position_id else None
        status = statuses.get(member_id, Attendance.AttendanceStatus.NO_RESPONSE)
        rows.append(
            {
                "member": member,
                "position": position.name if position else "",
                "position_order": position.ordering if position else 9999,
                "jersey_number": tm.jersey_number if tm is not None else None,
                "status": status,
                "available": tm is not None and status not in UNAVAILABLE_STATUSES,
                "on_roster": tm is not None,
                "selected": member_id in selected,
            }
        )
    rows.sort(key=lambda r: (r["position_order"], r["position"], r["member"].last_name, r["member"].first_name))
    return rows


def _pickable_ids(lineup) -> set:
    statuses = _statuses(lineup.event)
    return {member_id for member_id in _roster(lineup) if statuses.get(member_id, Attendance.AttendanceStatus.NO_RESPONSE) not in UNAVAILABLE_STATUSES}


@transaction.atomic
def toggle_selection(actor, lineup, member) -> bool:
    """Flip ``member``'s pick. Returns the new state (True = now selected). Picking needs an available roster player;
    un-picking always works. Nobody is told until the line-up is published."""
    require_lineup_manager(actor, lineup.event)
    Lineup.objects.select_for_update().filter(pk=lineup.pk).first()
    deleted, _details = LineupSelection.objects.filter(lineup=lineup, member=member).delete()
    if deleted:
        return False
    if member.pk not in _pickable_ids(lineup):
        _fail("member_id", "This player cannot be selected: not on the team's roster or not available.")
    LineupSelection.objects.create(lineup=lineup, member=member)
    return True


@transaction.atomic
def set_selection(actor, lineup, member_ids, slots=None) -> set:
    """Replace the whole pick with ``member_ids``. Newly picked ids must be available roster players (otherwise nothing
    is saved, ``Invalid`` on ``member_ids``); keeping an existing pick is always fine. Returns the selected ids.

    ``slots`` (``{slot key: member_id}``) replaces the slot assignments; ``None`` leaves them as they are. It needs the sport's
    slots switched on (else ``Invalid`` on ``slots`` unless empty); every slot must exist in the template and belong to a picked player
    (a slot holds one player; a player may hold several slots). Un-picking a player removes all their assignments."""
    require_lineup_manager(actor, lineup.event)
    Lineup.objects.select_for_update().filter(pk=lineup.pk).first()
    wanted = {_as_uuid(member_id) for member_id in member_ids}
    current = _selected_ids(lineup)
    invalid = wanted - current - _pickable_ids(lineup)
    if invalid:
        raise Invalid("Some of these players cannot be selected.", {"member_ids": sorted(str(i) for i in invalid)})
    assigned = _checked_slots(slots, wanted) if slots is not None else None
    before = dict(LineupSlotAssignment.objects.filter(lineup=lineup, member_id__in=wanted).values_list("slot", "member_id"))
    LineupSelection.objects.filter(lineup=lineup, member_id__in=current - wanted).delete()
    LineupSlotAssignment.objects.filter(lineup=lineup).exclude(member_id__in=wanted).delete()
    LineupSelection.objects.bulk_create([LineupSelection(lineup=lineup, member_id=member_id) for member_id in wanted - current])
    if assigned is not None:
        LineupSlotAssignment.objects.filter(lineup=lineup).delete()  # replace as a whole, so players can swap slots
        LineupSlotAssignment.objects.bulk_create([LineupSlotAssignment(lineup=lineup, member_id=member_id, slot=slot) for slot, member_id in assigned.items()])
        if before != assigned:
            audit.record(actor, "lineup.slots_changed", target=lineup, changes={"slots": {slot: str(member_id) for slot, member_id in sorted(assigned.items())}})
    return wanted


def _checked_slots(slots, picked) -> dict:
    """Validate a ``{slot key: member_id}`` map against the sport's template and the picked ids; blank members are dropped.
    (A slot holds one player by construction of the mapping; ``slots`` may also be a list of ``(slot, member_id)`` pairs, then a repeated slot is refused.)"""
    pairs = list(slots.items()) if hasattr(slots, "items") else list(slots or [])
    given = {}
    for slot, member_id in pairs:
        slot = (slot or "").strip()
        if not slot or not member_id:
            continue
        if slot in given:
            _fail("slots", "A slot can hold only one player.")
        given[slot] = _as_uuid(member_id)
    if not given:
        return {}
    enabled, template = slots_config()
    if not enabled:
        _fail("slots", "Line-up lines are not switched on for this sport.")
    valid = slot_keys(template)
    if any(slot not in valid for slot in given):
        _fail("slots", "Unknown line-up slot.")
    if set(given.values()) - set(picked):
        _fail("slots", "Only picked players can have a slot.")
    return given


def slots_config(club=None) -> tuple[bool, list]:
    """``(enabled, template)`` of the current club's sport (switched on only by platform admins)."""
    from club.models import Sport

    club = club or current_club()
    sport = Sport.objects.filter(pk=club.sport_id).first() if club is not None and club.sport_id else None
    if sport is None or not sport.lineup_slots_enabled or not isinstance(sport.lineup_template, list):
        return False, []
    return True, sport.lineup_template


def selection_slots(lineup, config=None) -> dict:
    """``{member_id: [slot keys]}`` of the picked players, only slots that exist in the sport's current template (else empty).
    Slot keys are in template order."""
    enabled, template = config if config is not None else slots_config()
    if not enabled or lineup is None or lineup.pk is None:
        return {}
    order = {key: i for i, key in enumerate(_ordered_slot_keys(template))}
    out: dict = {}
    for member_id, slot in LineupSlotAssignment.objects.filter(lineup=lineup).values_list("member_id", "slot"):
        if slot in order:
            out.setdefault(member_id, []).append(slot)
    return {m: sorted(v, key=order.__getitem__) for m, v in out.items()}


def _ordered_slot_keys(template) -> list[str]:
    keys = []
    for g in template if isinstance(template, list) else []:
        if isinstance(g, dict):
            for s in g.get("slots") or []:
                if isinstance(s, dict) and g.get("key") and s.get("key"):
                    keys.append(f"{g['key']}.{s['key']}")
    return keys


def slot_label(template, slot) -> str:
    """"Line 1 · C" for a slot key of ``template`` ("" when unknown)."""
    group_key, _dot, key = (slot or "").partition(".")
    for g in template if isinstance(template, list) else []:
        if g.get("key") == group_key:
            for sl in g.get("slots") or []:
                if sl.get("key") == key:
                    return f"{g.get('label', '')} · {sl.get('label', '')}"
    return ""


def _as_uuid(value):
    import uuid

    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        _fail("member_ids", f"Unknown member: {value}")


# --- the PDF overview -------------------------------------------------------------------------------------------------


def overview_context(lineup, *, now=None) -> dict:
    """What the PDF overview prints: the header (club, event, date/time, location, opponent), the template ``groups`` with every slot
    (``number`` / ``name`` of its player, ``None`` for an empty slot -- a player appears in each group they hold), ``loose`` (picked
    players without a slot), ``not_selected`` (roster players not picked) and the status. Without lines (feature off, empty template)
    the picked players are printed as ``by_position`` groups instead. Mirrors what ``selection_slots`` shows (unknown slots ignored)."""
    event = lineup.event
    rows = selectable_members(lineup)
    picked = [r for r in rows if r["selected"]]
    config = slots_config()
    enabled, template = config
    club = current_club()

    def person(row):
        return {"number": row["jersey_number"], "name": row["member"].get_full_name()}

    by_id = {r["member"].pk: r for r in picked}
    groups, loose, by_position = [], [], []
    if enabled and any(isinstance(g, dict) and g.get("slots") for g in template if isinstance(template, list)):
        owner = {}
        for member_id, slot_list in selection_slots(lineup, config).items():
            if member_id in by_id:
                for slot in slot_list:
                    owner[slot] = by_id[member_id]
        for g in template:
            if not isinstance(g, dict) or not g.get("key"):
                continue
            slots = []
            for sl in g.get("slots") or []:
                if isinstance(sl, dict) and sl.get("key"):
                    row = owner.get(f"{g['key']}.{sl['key']}")
                    slots.append({"label": sl.get("label", ""), "player": person(row) if row else None})
            groups.append({"label": g.get("label", ""), "slots": slots})
        placed = {r["member"].pk for r in owner.values()}
        loose = [person(r) for r in picked if r["member"].pk not in placed]
    else:
        current = None
        for r in picked:
            if current is None or current["label"] != r["position"]:
                current = {"label": r["position"], "players": []}
                by_position.append(current)
            current["players"].append(person(r))
    location = ""
    if event.location_id:
        location = ", ".join(part for part in (event.location.name, event.location.city) if part)
    return {
        "club": club,
        "event": event,
        "when": timezone.localtime(event.start),
        "location": location,
        "opponent": event.opponent.name if event.opponent_id else "",
        "team": lineup.team,
        "groups": groups,
        "loose": loose,
        "by_position": by_position,
        "picked_count": len(picked),
        "not_selected": len(rows) - len(picked),
        "status": status(lineup),
        "generated_at": now or timezone.localtime(),
    }


def render_overview_pdf(lineup) -> bytes:
    """The overview as a PDF (WeasyPrint). Raises ``shop.services.invoices.PDFUnavailable`` when it cannot render here."""
    from shop.services import invoices

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise invoices.PDFUnavailable("PDF rendering is not available on this server.") from error
    context = overview_context(lineup)
    context["logo_data_uri"] = invoices._logo_uri(context["club"])
    return HTML(string=render_to_string("events/lineup_overview.html", context)).write_pdf()


# --- publishing -------------------------------------------------------------------------------------------------------


def changes_on_publish(lineup) -> tuple[set, set]:
    """``(newly_selected, newly_dropped)`` member ids: who publishing now would tell "you're in" and who "you're not in
    the line-up this time", measured against who is SELECTED in the attendance right now (the last publish)."""
    previously_selected = set(Attendance.objects.filter(event=lineup.event, status=Attendance.AttendanceStatus.SELECTED).values_list("member_id", flat=True))
    selected = _selected_ids(lineup)
    return selected - previously_selected, previously_selected - selected


def status(lineup) -> str:
    """``none`` / ``draft`` / ``scheduled`` / ``published``, as the old screens showed it."""
    if lineup is None:
        return "none"
    if lineup.published_at is not None:
        return "published"
    if lineup.scheduled_publish_at is not None:
        return "scheduled"
    return "draft" if lineup.selections.exists() else "none"


def publish_lineup(actor, lineup) -> dict:
    """Publish (or "publish changes"): writes the picks into the attendance and tells only the people whose situation
    changed. Returns ``{"selected": ids newly told they're in, "dropped": ids told they're out}``."""
    require_lineup_manager(actor, lineup.event)
    if lineup.published_at is None and not lineup.selections.exists():
        _fail("member_ids", "Pick players before publishing.")
    return _publish(actor, lineup)


@transaction.atomic
def _publish(actor, lineup, *, club=None) -> dict:
    """The publish itself, without the permission check: also what the scheduled sweep calls (``actor`` None)."""
    given = lineup
    lineup = Lineup.objects.select_for_update().select_related("event").get(pk=lineup.pk)
    event = lineup.event
    newly_selected, newly_dropped = changes_on_publish(lineup)
    selected = _selected_ids(lineup)

    lineup.published_at = timezone.now()
    lineup.scheduled_publish_at = None
    lineup.save(update_fields=["published_at", "scheduled_publish_at"])
    given.published_at, given.scheduled_publish_at = lineup.published_at, None  # keep the caller's instance current

    # Remember what each player had answered (only when it is not a line-up status already), so unpublishing can put it back.
    published_statuses = [Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.NOT_SELECTED]
    Attendance.objects.filter(event=event).exclude(status__in=published_statuses).filter(Q(member_id__in=selected) | ~Q(status__in=UNAVAILABLE_STATUSES)).update(status_before_lineup=F("status"))
    Attendance.objects.filter(event=event, member_id__in=selected).update(status=Attendance.AttendanceStatus.SELECTED)
    Attendance.objects.filter(event=event).exclude(member_id__in=selected).exclude(status__in=UNAVAILABLE_STATUSES).update(status=Attendance.AttendanceStatus.NOT_SELECTED)

    when = timezone.localtime(event.start).strftime("%d/%m/%Y %H:%M")
    _notify_members(
        Member.objects.filter(pk__in=newly_selected).select_related("user"),
        title=f"You're in the line-up for {event.title}",
        body=f"{event.title}, {when}.",
        kind=inbox.Kind.SUCCESS,
        href=_href(event),
        category=Category.LINEUP,
        club=club,
    )
    _notify_members(
        Member.objects.filter(pk__in=newly_dropped).select_related("user"),
        title="You're not in the line-up this time",
        body=f"The line-up for {event.title} ({when}) has changed: you're not in it this time.",
        kind=inbox.Kind.INFO,
        href=_href(event),
        category=Category.LINEUP,
        club=club,
    )
    audit.record(actor, "lineup.published", target=lineup, changes={"selected": sorted(map(str, selected)), "newly_selected": sorted(map(str, newly_selected)), "dropped": sorted(map(str, newly_dropped))})
    return {"selected": newly_selected, "dropped": newly_dropped}


@transaction.atomic
def unpublish_lineup(actor, lineup) -> Lineup:
    """Take a published line-up back to a draft: the picks stay, "selected" / "not selected" players get their earlier answer back, and the
    people who had been told they were in are told it was withdrawn. A no-op for a line-up that is not published."""
    require_lineup_manager(actor, lineup.event)
    lineup = Lineup.objects.select_for_update().select_related("event").get(pk=lineup.pk)
    if lineup.published_at is None:
        return lineup
    event = lineup.event
    was_selected = set(Attendance.objects.filter(event=event, status=Attendance.AttendanceStatus.SELECTED).values_list("member_id", flat=True))
    lineup.published_at = None
    lineup.save(update_fields=["published_at"])
    # Everyone goes back to what they had answered before the publish (present when that was not recorded, e.g. a line-up published earlier).
    for row in Attendance.objects.filter(event=event, status__in=[Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.NOT_SELECTED]):
        row.status, row.status_before_lineup = row.status_before_lineup or Attendance.AttendanceStatus.PRESENT, ""
        row.save(update_fields=["status", "status_before_lineup", "modified"])
    when = timezone.localtime(event.start).strftime("%d/%m/%Y %H:%M")
    _notify_members(
        Member.objects.filter(pk__in=was_selected).select_related("user"),
        title=f"The line-up for {event.title} was withdrawn",
        body=f"{event.title}, {when}. A new line-up will follow.",
        kind=inbox.Kind.INFO,
        href=_href(event),
        category=Category.LINEUP,
    )
    audit.record(actor, "lineup.unpublished", target=lineup, changes={"was_selected": sorted(map(str, was_selected))})
    return lineup


@transaction.atomic
def schedule_publish(actor, lineup, when) -> Lineup:
    """Publish ``lineup`` automatically at ``when`` (aware, in the future). Overwrites an earlier schedule."""
    require_lineup_manager(actor, lineup.event)
    if when is None or timezone.is_naive(when) or when <= timezone.now():
        _fail("scheduled_publish_at", "Pick a date and time in the future.")
    if not lineup.selections.exists():
        _fail("member_ids", "Pick players before publishing.")
    changes = audit.diff(lineup, {"scheduled_publish_at": when})
    lineup.scheduled_publish_at = when
    lineup.save(update_fields=["scheduled_publish_at"])
    audit.record(actor, "lineup.scheduled", target=lineup, changes=changes)
    return lineup


@transaction.atomic
def unschedule(actor, lineup) -> Lineup:
    """Cancel a scheduled publish (no-op when none is set)."""
    require_lineup_manager(actor, lineup.event)
    if lineup.scheduled_publish_at is not None:
        changes = audit.diff(lineup, {"scheduled_publish_at": None})
        lineup.scheduled_publish_at = None
        lineup.save(update_fields=["scheduled_publish_at"])
        audit.record(actor, "lineup.unscheduled", target=lineup, changes=changes)
    return lineup


def publish_due_lineups(*, now=None, club=None) -> int:
    """Publish every line-up of the current schema whose scheduled time has come (the system, no actor)."""
    now = now or timezone.now()
    count = 0
    for lineup in Lineup.objects.filter(published_at__isnull=True, scheduled_publish_at__isnull=False, scheduled_publish_at__lte=now):
        _publish(None, lineup, club=club)
        count += 1
    return count


# --- dropouts ---------------------------------------------------------------------------------------------------------


def event_managers(event):
    """Members with a management position on one of the event's teams in the event's season."""
    season = event_season(event)
    if season is None:
        return Member.objects.none()
    ids = StaffAssignment.objects.filter(team__in=event.teams.all(), season=season, position__management_position=True).values_list("member_id", flat=True)
    return Member.objects.filter(pk__in=ids).select_related("user")


def notify_coach_dropout(event, member, coach) -> int:
    """A manager (``coach``: a Member or a User) marked a selected ``member`` absent: the event's OTHER managers with a
    login hear about it. Nothing goes to the player and no coach note is ever part of the text. Returns how many were
    told."""
    coach_member = coach if isinstance(coach, Member) else Member.objects.filter(user=coach).first() if coach is not None else None
    coach_user_id = coach_member.user_id if coach_member is not None else getattr(coach, "pk", None)
    managers = event_managers(event)
    if coach_member is not None:
        managers = managers.exclude(pk=coach_member.pk)
    if coach_user_id is not None:
        managers = managers.exclude(user_id=coach_user_id)
    coach_name = coach_member.get_full_name() if coach_member is not None else "A coach"
    told = _notify_members(
        managers,
        title="Line-up dropout",
        body=f"{coach_name} marked {member.get_full_name()} as out of the line-up for {event.title}.",
        kind=inbox.Kind.WARNING,
        href=_href(event),
        category=Category.COACH,
    )
    return len(told)


def notify_coach_withdrawal(event, member, actor, reason: str = "") -> int:
    """``member`` (answering themselves or through a guardian, ``actor``: a Member or a User) said they can't make it after all
    once the reply deadline had passed: the event's managers with a login hear about it, the same people and channel as a
    line-up dropout, with the reason given. Returns how many were told."""
    actor_member = actor if isinstance(actor, Member) else Member.objects.filter(user=actor).first() if actor is not None else None
    actor_user_id = actor_member.user_id if actor_member is not None else getattr(actor, "pk", None)
    managers = event_managers(event)
    if actor_member is not None:
        managers = managers.exclude(pk=actor_member.pk)
    if actor_user_id is not None:
        managers = managers.exclude(user_id=actor_user_id)
    reason = (reason or "").strip()
    told = _notify_members(
        managers,
        title="Late withdrawal",
        body=f"{member.get_full_name()} can't make it to {event.title} after all (after the reply deadline)." + (f" Reason: {reason}" if reason else ""),
        kind=inbox.Kind.WARNING,
        href=_href(event),
        category=Category.COACH,
    )
    return len(told)
