"""Referee management: assigning referees and match officials to home games and keeping track of what they are paid.

Everything here is behind ``events.manage_referees`` (``members.services.access.require_referees``) and, at the API layer, the
``referee_officials`` feature. Two kinds share every rule (``KINDS``): ``referee`` (``EventReferee``, ``Event.max_referees``,
``Team.referee_management``, ``teams.RefereeProfile``) and ``official`` (the same with official in each place).

* A game needs club-arranged people when it is a home game or tournament (``Event.is_home_fixture``) with at least one team the
  club arranges them for (``Team.<kind>_management == club``). Federation-managed teams are left out entirely.
* Eligibility comes from the member's profile: a level that qualifies for one of the game's teams (own or inherited) minus the
  explicitly excluded teams. A lapsed validity is still listed, flagged ``expired`` (managers may still pick them).
* The capacity ``Event.max_<kind>s`` is a hard ceiling. Schedule conflicts are information only, never a block.
* Fees are stored per assignment (fee, kilometres, rate per km, "no fee"), which the payment form (``payment_forms``) prints.

Not ported from the old app: sign-up invites / acceptances and the notifications sent on assignment."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from club.models import RefereeType
from events.models import ASSUMED_EVENT_DURATION, Attendance, Event, EventOfficial, EventReferee, OfficialSignup, RefereeSignup, Season
from events.services import referee_notices
from members.models import Member
from members.services import access, audit
from members.services.errors import Invalid
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

RANGES = ("week", "two_weeks", "10", "25", "50")
BUSY_STATUSES = (Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED, Attendance.AttendanceStatus.MAYBE, Attendance.AttendanceStatus.NO_RESPONSE)
NEEDS = ("all", "open", "referees", "officials", "fees")


@dataclass(frozen=True)
class Kind:
    code: str
    model: type
    profile: type
    level: type
    related: str  # Event reverse accessor
    team_field: str
    max_field: str
    type_kind: str
    signup: type
    signup_related: str


KINDS = {
    "referee": Kind("referee", EventReferee, RefereeProfile, RefereeLevel, "referees", "referee_management", "max_referees", RefereeType.REFEREE, RefereeSignup, "referee_signups"),
    "official": Kind("official", EventOfficial, OfficialProfile, OfficialLevel, "officials", "official_management", "max_officials", RefereeType.OFFICIAL, OfficialSignup, "official_signups"),
}


def kind_of(code: str) -> Kind:
    if code not in KINDS:
        raise Invalid("Unknown kind.", {"kind": ["Choose referee or official."]})
    return KINDS[code]


# -- which games ---------------------------------------------------------------------------------------------------------


def club_managed_team_ids(event, kind: Kind) -> set:
    return {team.pk for team in event.teams.all() if getattr(team, kind.team_field) == Team.Management.CLUB}


def needs_management(event, kind: Kind) -> bool:
    """A home game or tournament with at least one team the club arranges this kind of person for."""
    return event.is_home_fixture and bool(club_managed_team_ids(event, kind))


def _managed_games():
    """Every home game or tournament that needs people or already has some, soonest first (callers filter and slice)."""
    managed = Q(teams__referee_management=Team.Management.CLUB) | Q(teams__official_management=Team.Management.CLUB)
    assigned = Q(referees__isnull=False) | Q(officials__isnull=False)
    return (
        Event.objects.filter(kind__in=(Event.EventKind.GAME, Event.EventKind.TOURNAMENT), location__is_home=True, cancelled=False)
        .filter(managed | assigned)
        .distinct()
        .select_related("location", "opponent")
        .prefetch_related("teams", "referees__member", "referees__referee_type", "officials__member", "officials__referee_type", "referee_signups__member", "official_signups__member")
    )


def _season_end(season) -> datetime.date | None:
    return season.end_date if season else None


# -- types ---------------------------------------------------------------------------------------------------------------


def types_for(club, kind: Kind):
    """The club's sport's types of this kind, in form order."""
    return RefereeType.objects.filter(sport_id=club.sport_id, kind=kind.type_kind).order_by("ordering", "name")


def resolve_type(club, kind: Kind, type_id):
    if type_id in (None, ""):
        return None
    referee_type = types_for(club, kind).filter(pk=type_id).first()
    if referee_type is None:
        raise Invalid("This type isn't available for your club's sport.", {"referee_type_id": ["This type isn't available for your club's sport."]})
    return referee_type


def order_assignments(rows):
    """Display order used by every screen and the PDF: by type order (untyped last), then by name."""

    def key(row):
        t = row.referee_type
        return (t is None, t.ordering if t else 0, t.name.casefold() if t else "", row.display_name.casefold(), str(row.pk))

    return sorted(rows, key=key)


# -- eligibility ---------------------------------------------------------------------------------------------------------


def candidates(event, kind: Kind, q: str = "") -> list[dict]:
    """People who could take this game: ``[{id, name, level, expired, eligible, signed_up, conflicts}]``, free people first, then
    those who signed up, expired last. Already assigned people are left out. Empty when the game needs nobody of this kind.
    With a search ``q`` the eligible people matching it are followed by up to 15 other members matching it (``eligible``
    false, no level): anyone can be assigned by hand, e.g. when nobody is eligible."""
    if not needs_management(event, kind):
        return []
    team_ids = club_managed_team_ids(event, kind)
    by_level = {level.pk: level.eligible_team_ids() for level in kind.level.objects.select_related("inherits_from")}
    today = timezone.localdate()
    assigned = set(getattr(event, kind.related).values_list("member_id", flat=True))
    signed = set(getattr(event, kind.signup_related).values_list("member_id", flat=True))
    profiles = kind.profile.objects.filter(level__isnull=False, valid_until__isnull=False).select_related("member", "level")
    if kind.code == "referee":
        profiles = profiles.prefetch_related("excluded_teams")
    found = []
    for profile in profiles:
        if profile.member_id in assigned:
            continue
        allowed = profile.eligible_team_ids(by_level.get(profile.level_id, set()), ignore_validity=True) if kind.code == "referee" else set(by_level.get(profile.level_id, set()))
        if allowed & team_ids:
            found.append(profile)
    conflicts = conflicts_for([p.member_id for p in found], event)
    out = [{"id": p.member_id, "name": str(p.member), "level": p.level.name, "expired": p.valid_until < today, "eligible": True, "signed_up": p.member_id in signed, "conflicts": conflicts.get(p.member_id, [])} for p in found]
    out.sort(key=lambda row: (bool(row["conflicts"]), row["expired"], not row["signed_up"], row["name"].casefold()))
    words = (q or "").split()
    if words:
        out = [row for row in out if all(w.casefold() in row["name"].casefold() for w in words)]
        out.extend(_other_members(event, out, assigned, words))
    return out


def _other_members(event, listed: list[dict], assigned: set, words: list[str], limit: int = 15) -> list[dict]:
    """Members matching ``words`` who are neither listed nor assigned yet, as not-eligible candidates."""
    people = Member.objects.exclude(pk__in={row["id"] for row in listed} | set(assigned))
    for word in words:
        people = people.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word))
    people = list(people.order_by("last_name", "first_name")[:limit])
    conflicts = conflicts_for([m.pk for m in people], event)
    return [{"id": m.pk, "name": str(m), "level": "", "expired": False, "eligible": False, "signed_up": False, "conflicts": conflicts.get(m.pk, [])} for m in people]


def candidate_for(event, kind: Kind, member) -> dict | None:
    """``member`` as a candidate for this game: an eligible one if they are, else any member (``eligible`` false) as long as the
    game needs this kind of person and they are not on it yet."""
    if not needs_management(event, kind):
        return None
    match = next((c for c in candidates(event, kind) if c["id"] == member.pk), None)
    if match is not None:
        return match
    if getattr(event, kind.related).filter(member=member).exists():
        return None
    return _other_members_row(event, member)


def _other_members_row(event, member) -> dict:
    return {"id": member.pk, "name": str(member), "level": "", "expired": False, "eligible": False, "signed_up": False, "conflicts": conflicts_for([member.pk], event).get(member.pk, [])}


def _window(event):
    return event.start, event.end or (event.start + ASSUMED_EVENT_DURATION)


def conflicts_for(member_ids, event) -> dict:
    """``{member_id: [{event_id, title, start, end, kind, role}]}``: the other events overlapping ``event`` that these members are
    on, as a referee / official elsewhere (``role`` referee, official) or on the attendance list with an open answer or a yes
    (``role`` attendance). Information only, never a block on its own."""
    member_ids = set(member_ids)
    if not member_ids:
        return {}
    start, end = _window(event)
    nearby = Event.objects.exclude(pk=event.pk).filter(cancelled=False, start__lt=end, start__gt=start - datetime.timedelta(days=2))
    overlapping = {e.pk: e for e in nearby if _window(e)[1] > start}
    out: dict = {}
    if not overlapping:
        return out

    def add(member_id, other, role):
        s, e = _window(other)
        rows = out.setdefault(member_id, [])
        if not any(r["event_id"] == other.pk and r["role"] == role for r in rows):
            rows.append({"event_id": other.pk, "title": other.title, "start": s, "end": e, "kind": other.kind, "role": role})

    for kind in KINDS.values():
        for row in kind.model.objects.filter(member_id__in=member_ids, event_id__in=overlapping):
            add(row.member_id, overlapping[row.event_id], kind.code)
    for row in Attendance.objects.filter(member_id__in=member_ids, event_id__in=overlapping, status__in=BUSY_STATUSES):
        add(row.member_id, overlapping[row.event_id], "attendance")
    for rows in out.values():
        rows.sort(key=lambda r: r["start"])
    return out


# -- writes --------------------------------------------------------------------------------------------------------------


def _lock(event, kind: Kind) -> Event:
    event = Event.objects.select_for_update().get(pk=event.pk)
    if not needs_management(event, kind):
        raise Invalid(f"A {kind.code} can only be assigned to a home game of a team the club arranges them for.")
    if getattr(event, kind.related).count() >= getattr(event, kind.max_field):
        raise Invalid(f"This game already has its maximum of {getattr(event, kind.max_field)} {kind.code}(s).")
    return event


def _assigned_by(actor):
    return Member.objects.filter(user=actor).first() if actor is not None and getattr(actor, "pk", None) else None


@transaction.atomic
def assign(actor, event, kind: Kind, *, member_id=None, external_name: str = "", type_id=None, force: bool = False):
    """Put a club member (``member_id``) or an external person (``external_name``) on a game."""
    access.require_referees(actor)
    external_name = (external_name or "").strip()
    if bool(member_id) == bool(external_name):
        raise Invalid("Choose a member or give a name.", {"member_id": ["Choose a member or give a name."]})
    referee_type = resolve_type(connection.tenant, kind, type_id)
    member = None
    if member_id:
        member = Member.objects.filter(pk=member_id).first()
        match = candidate_for(event, kind, member) if member else None
        if match is None:
            raise Invalid("This person can't be assigned to this game.", {"member_id": ["This person can't be assigned to this game."]})
        if match["conflicts"] and not force:
            titles = ", ".join(c["title"] for c in match["conflicts"])
            raise Invalid(f"{member} is already expected elsewhere at this time: {titles}. Confirm to assign anyway.", {"member_id": ["Has another event at the same time."]}, code="conflict")
    event = _lock(event, kind)
    if member and getattr(event, kind.related).filter(member=member).exists():
        raise Invalid(f"{member} is already assigned to this game.", {"member_id": ["Already assigned."]})
    row = kind.model.objects.create(event=event, member=member, external_name=external_name, referee_type=referee_type, assigned_by=_assigned_by(actor))
    if member:
        kind.signup.objects.filter(event=event, member=member).delete()  # a pick settles the sign-up
        referee_notices.notify_member(member, event, kind.code, assigned=True, actor=actor)
    audit.record(actor, f"event.{kind.code}_assigned", member=member, target=row, target_label=f"{row.display_name} - {event.title}"[:255], changes={"event": {"from": None, "to": str(event.pk)}})
    return row


@transaction.atomic
def add_signup(actor, event, kind: Kind, member_id):
    """Register a sign-up for an eligible member on their behalf (a member app will call the same service later)."""
    access.require_referees(actor)
    member = Member.objects.filter(pk=member_id).first()
    if member is None or not any(c["id"] == member.pk for c in candidates(event, kind)):
        raise Invalid("This person can't sign up for this game.", {"member_id": ["This person can't sign up for this game."]})
    signup, created = kind.signup.objects.get_or_create(event=event, member=member)
    if created:
        audit.record(actor, f"event.{kind.code}_signup_added", member=member, target=signup, target_label=f"{member} - {event.title}"[:255])
    return signup


@transaction.atomic
def remove_signup(actor, signup, kind: Kind) -> None:
    access.require_referees(actor)
    audit.record(actor, f"event.{kind.code}_signup_removed", member=signup.member, target=signup, target_label=f"{signup.member} - {signup.event.title}"[:255])
    signup.delete()


FEE_FIELDS = ("fee", "km", "km_rate", "no_fee")


@transaction.atomic
def update(actor, row, kind: Kind, changes: dict):
    """Change the fee line (fee, km, km_rate, no_fee), the type, or the name of an external person. Only keys in ``changes``
    are touched. ``no_fee`` clears the fee line, like the old form did."""
    access.require_referees(actor)
    errors = {}
    values = {}
    for name in FEE_FIELDS:
        if name in changes:
            values[name] = changes[name]
    for name, limit in (("fee", Decimal("999999.99")), ("km", Decimal("99999.9")), ("km_rate", Decimal("99.9999"))):
        value = values.get(name)
        if value is not None and (value < 0 or value > limit):
            errors[name] = ["Must be a positive amount within range."]
    if "name" in changes:
        name = (changes["name"] or "").strip()
        if not row.is_external:
            errors["name"] = ["Only an external person can be renamed."]
        elif not name:
            errors["name"] = ["A name is required."]
        else:
            values["external_name"] = name
    if "referee_type_id" in changes:
        try:
            values["referee_type"] = resolve_type(connection.tenant, kind, changes["referee_type_id"])
        except Invalid as e:
            errors.update(e.errors)
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)
    if values.get("no_fee", row.no_fee):
        values.update(fee=Decimal("0.00"), km=None, km_rate=None)
        values.setdefault("no_fee", row.no_fee)
    elif "fee" in values and values["fee"] is None:
        values["fee"] = Decimal("0.00")
    diff = audit.diff(row, {k: v for k, v in values.items() if k != "referee_type"} | ({"referee_type_id": values["referee_type"].pk if values["referee_type"] else None} if "referee_type" in values else {}))
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    if diff:
        audit.record(actor, f"event.{kind.code}_updated", member=row.member, target=row, target_label=f"{row.display_name} - {row.event.title}"[:255], changes=diff)
    return row


@transaction.atomic
def remove(actor, row, kind: Kind) -> None:
    access.require_referees(actor)
    label = f"{row.display_name} - {row.event.title}"[:255]
    member, pk, event = row.member, row.pk, row.event
    row.delete()
    referee_notices.notify_member(member, event, kind.code, assigned=False, actor=actor)
    entry = audit.record(actor, f"event.{kind.code}_removed", member=member, changes={"event": {"from": str(row.event_id), "to": None}})
    entry.target_type, entry.target_id, entry.target_label = f"event{kind.code}", str(pk), label
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def set_capacity(actor, event, *, max_referees=None, max_officials=None):
    """Raise or lower how many of each a game takes. Not below what is already assigned."""
    access.require_referees(actor)
    errors = {}
    for kind, value in ((KINDS["referee"], max_referees), (KINDS["official"], max_officials)):
        if value is None:
            continue
        if value < 0 or value > 20:
            errors[kind.max_field] = ["Choose a number between 0 and 20."]
        elif value < getattr(event, kind.related).count():
            errors[kind.max_field] = ["More people are already assigned."]
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)
    changes = {k: v for k, v in (("max_referees", max_referees), ("max_officials", max_officials)) if v is not None}
    diff = audit.diff(event, changes)
    for name, value in changes.items():
        setattr(event, name, value)
    event.save(update_fields=list(changes) or None)
    if diff:
        audit.record(actor, "event.referee_capacity_updated", target=event, target_label=event.title[:255], changes=diff)
    return event


# -- reading -------------------------------------------------------------------------------------------------------------


def assignment_out(row, conflicts=()) -> dict:
    return {
        "id": row.pk,
        "member_id": row.member_id,
        "name": row.display_name,
        "is_external": row.is_external,
        "type_id": row.referee_type_id,
        "type_name": row.referee_type.name if row.referee_type_id else "",
        "fee": row.fee or Decimal("0.00"),
        "km": row.km,
        "km_rate": row.km_rate,
        "no_fee": row.no_fee,
        "fee_pending": row.fee_pending,
        "total": row.total_payable,
        "conflicts": list(conflicts),
    }


def game_out(event, *, conflicts: bool = False, detail: bool = False) -> dict:
    """The API shape of a game. ``conflicts`` adds the double bookings of the assigned people; ``detail`` also works out, per
    sign-up, whether the person can be assigned right now and what they would clash with."""
    out = {
        "id": event.pk,
        "title": event.title,
        "kind": event.kind,
        "start": event.start,
        "end": _window(event)[1],
        "location_name": event.location.name if event.location_id else "",
        "opponent_name": event.opponent.name if event.opponent_id else "",
        "teams": [{"id": t.pk, "name": t.name, "short_name": t.short_name} for t in event.teams.all()],
    }
    total = Decimal("0")
    pending = False
    signups = []
    clashes = 0
    for kind in KINDS.values():
        rows = order_assignments(getattr(event, kind.related).all())
        found = conflicts_for([r.member_id for r in rows if r.member_id], event) if conflicts else {}
        clashes += len(found)
        total += sum((r.total_payable for r in rows), Decimal("0"))
        pending = pending or any(r.fee_pending for r in rows)
        out[kind.related] = [assignment_out(r, found.get(r.member_id, ())) for r in rows]
        out[f"max_{kind.related}"] = getattr(event, kind.max_field)
        out[f"needs_{kind.related}"] = needs_management(event, kind)
        out[f"{kind.code}_short"] = out[f"needs_{kind.related}"] and len(rows) < getattr(event, kind.max_field)
        sign_rows = list(getattr(event, kind.signup_related).all())
        free = {c["id"]: c for c in candidates(event, kind)} if detail and sign_rows and len(rows) < getattr(event, kind.max_field) else {}
        for sign in sign_rows:
            match = free.get(sign.member_id)
            signups.append({"id": sign.pk, "kind": kind.code, "member_id": sign.member_id, "name": str(sign.member), "created": sign.created, "can_assign": match is not None, "conflicts": match["conflicts"] if match else []})
    out["signups"] = signups
    out["fees_pending"] = pending
    out["conflict_count"] = clashes
    out["total"] = total
    return out


def game_detail(event) -> dict:
    return game_out(event, conflicts=True, detail=True)


def _matches(game: dict, need: str) -> bool:
    if need == "open":
        return game["referee_short"] or game["official_short"]
    if need == "referees":
        return game["referee_short"]
    if need == "officials":
        return game["official_short"]
    if need == "fees":
        return game["fees_pending"]
    return True


def _season_bounds():
    season = Season.get_current()
    return (season.start_date, season.end_date) if season else (None, None)


def games(*, scope: str = "upcoming", range_: str = "two_weeks", need: str = "all") -> dict:
    """The management list. ``upcoming``: managed games from now on, narrowed by ``range_`` (a calendar window or a count)
    and then by ``need``. ``season``: every game of the current season that has someone assigned, past and upcoming, in date
    order (the payments tab). ``counts`` holds the size of every ``need`` chip over the same window."""
    now = timezone.now()
    if range_ not in RANGES:
        range_ = "two_weeks"
    if need not in NEEDS:
        need = "all"
    qs = _managed_games()
    if scope == "season":
        first, last = _season_bounds()
        if first:
            qs = qs.filter(start__date__gte=first, start__date__lte=last)
        rows = [game_out(e) for e in qs.order_by("start")]
        rows = [g for g in rows if g["referees"] or g["officials"]]
    else:
        events = list(qs.filter(start__gte=now).order_by("start"))
        if range_ in ("week", "two_weeks"):
            today = timezone.localdate()
            end = today + datetime.timedelta(days=6 - today.weekday()) + datetime.timedelta(days=7 if range_ == "two_weeks" else 0)
            events = [e for e in events if timezone.localtime(e.start).date() <= end]
        else:
            events = events[: int(range_)]
        rows = [game_out(e, conflicts=True) for e in events]
        rows = [g for g in rows if g["needs_referees"] or g["needs_officials"] or g["referees"] or g["officials"]]
    counts = {key: sum(1 for g in rows if _matches(g, key)) for key in NEEDS}
    return {"games": [g for g in rows if _matches(g, need)], "counts": counts, "range": range_, "need": need, "scope": scope}


def summary() -> dict:
    """KPIs over the rest of the season, the workload per person this season and what the club can choose from."""
    now = timezone.now()
    season = Season.get_current()
    upcoming = [e for e in _managed_games().filter(start__gte=now) if not season or timezone.localtime(e.start).date() <= season.end_date]
    kpis = {}
    for kind in KINDS.values():
        managed = [e for e in upcoming if needs_management(e, kind)]
        counts = [len(getattr(e, kind.related).all()) for e in managed]
        maxes = [getattr(e, kind.max_field) for e in managed]
        kpis[kind.code] = {
            "games": len(managed),
            # Every game lands in exactly one bucket; a game that needs nobody of this kind (max 0) is fully staffed.
            "none": sum(1 for c, m in zip(counts, maxes, strict=True) if m > 0 and c == 0),
            "partial": sum(1 for c, m in zip(counts, maxes, strict=True) if 0 < c < m),
            "full": sum(1 for c, m in zip(counts, maxes, strict=True) if c >= m),
        }
    workload: dict = {}
    since = season.start_date if season else None
    for kind in KINDS.values():
        rows = kind.model.objects.select_related("member", "event")
        if season:
            rows = rows.filter(event__start__date__gte=season.start_date, event__start__date__lte=season.end_date)
        for row in rows:
            key = (kind.code, row.member_id or row.external_name.casefold())
            entry = workload.setdefault(key, {"key": f"{kind.code}:{key[1]}", "member_id": row.member_id, "name": row.display_name, "kind": kind.code, "games": 0, "total": Decimal("0")})
            entry["games"] += 1
            entry["total"] += row.total_payable
    pending = 0
    for kind in KINDS.values():
        rows = kind.model.objects.filter(fee=0, no_fee=False)
        if season:
            rows = rows.filter(event__start__date__gte=since, event__start__date__lte=season.end_date)
        pending += rows.count()
    return {
        "season_name": season.name if season else "",
        "kpis": kpis,
        "fees_pending": pending,
        "workload": sorted(workload.values(), key=lambda w: (-w["games"], w["name"].casefold())),
    }


def club_types(club) -> dict:
    return {kind.code: [{"id": t.pk, "name": t.name} for t in types_for(club, kind)] for kind in KINDS.values()}
