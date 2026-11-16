"""Teams, rosters, staff and the jersey-number views. Everyone reads; creating, deleting and ordering teams needs the
settings permission, editing a team, its roster and its staff a management position on that team. Writes are audited.

Audit actions: ``team.created`` / ``team.updated`` / ``team.deleted`` (club level, target = the team) and, about the
member: ``team.roster_added`` / ``team.roster_updated`` / ``team.roster_removed`` /
``team.staff_added`` / ``team.staff_updated`` / ``team.staff_removed`` (target = the membership / assignment).

Captains: at most one captain per team and season; making someone captain clears the previous one, and a member
is never captain and alternate captain at once (the one just set wins). Several alternates are allowed."""

import itertools
import uuid

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.deletion import ProtectedError, RestrictedError

from events.models import Season
from members.models import Member
from members.services import access, audit
from members.services.access import can_manage_settings, require_settings, require_team_manager
from members.services.errors import Invalid, NotAllowed
from teams.models import NumberPool, NumberReservation, Position, StaffAssignment, Team, TeamMembership, TeamPhoto

from . import numbers

FIELDS = ("name", "short_name", "pool_id", "age_min", "age_max", "tournament_only", "ordering", "feeds_into_id", "referee_management", "official_management")
STRUCTURE_FIELDS = ("pool_id", "age_min", "age_max", "feeds_into_id", "referee_management", "official_management")  # need the settings permission to change
MSG = "Please correct the highlighted fields."


def _fail(field, message):
    raise Invalid(message, {field: [message]})


def current_season(season_id=None):
    """The season with ``season_id``, else the current one (covering today); ``None`` if there is none."""
    if season_id is not None:
        return Season.objects.filter(pk=season_id).first()
    return Season.get_current()


def resolve_season(season_id, field="season_id"):
    season = current_season(season_id)
    if season is None:
        _fail(field, "Unknown season." if season_id else "There is no current season.")
    return season


def get_team(team_id):
    return Team.objects.select_related("pool", "feeds_into").filter(pk=team_id).first()


def team_out(team) -> dict:
    return {
        "id": team.pk,
        "name": team.name,
        "short_name": team.short_name,
        "pool_id": team.pool_id,
        "pool_name": team.pool.name if team.pool_id else None,
        "age_min": team.age_min,
        "age_max": team.age_max,
        "tournament_only": team.tournament_only,
        "ordering": team.ordering,
        "feeds_into_id": team.feeds_into_id,
        "feeds_into_name": team.feeds_into.name if team.feeds_into_id else None,
        "referee_management": team.referee_management,
        "official_management": team.official_management,
    }


def teams_without_coach(season):
    """Teams where nobody on the staff holds a management position in ``season`` (the overview counts the same)."""
    coaches = StaffAssignment.objects.filter(team=OuterRef("pk"), season=season, position__management_position=True)
    return Team.objects.exclude(Exists(coaches))


def list_teams(actor, season, q: str = "", coach: str = "any") -> list[dict]:
    qs = Team.objects.select_related("pool", "feeds_into")
    if coach == "none":
        qs = qs.filter(pk__in=teams_without_coach(season).values("pk"))
    if q.strip():
        qs = qs.filter(Q(name__icontains=q.strip()) | Q(short_name__icontains=q.strip()))
    qs = qs.annotate(
        player_count=Count("roster", filter=Q(roster__season=season), distinct=True),
        staff_count=Count("staff_assignments", filter=Q(staff_assignments__season=season), distinct=True),
    ).order_by(F("ordering").asc(nulls_last=True), "name")
    return [{**team_out(t), "player_count": t.player_count, "staff_count": t.staff_count} for t in qs]


def _validate(team, values):
    errors = {}
    name = (values.get("name") or "").strip()
    if not name:
        errors["name"] = ["This field is required."]
    elif Team.objects.filter(name__iexact=name).exclude(pk=team.pk).exists():
        errors["name"] = ["A team with this name already exists."]
    pool = None
    if values.get("pool_id") is not None:
        pool = NumberPool.objects.filter(pk=values["pool_id"]).first()
        if pool is None:
            errors["pool_id"] = ["Unknown number pool."]
    feeds = None
    if values.get("feeds_into_id") is not None:
        feeds = Team.objects.filter(pk=values["feeds_into_id"]).first()
        if feeds is None:
            errors["feeds_into_id"] = ["Unknown team."]
        elif team.pk and feeds.pk == team.pk:
            errors["feeds_into_id"] = ["A team can't feed into itself."]
        else:  # loop: following feeds_into from the target must not come back to this team
            seen, cur = set(), feeds
            while cur is not None and cur.pk not in seen:
                if team.pk and cur.pk == team.pk:
                    errors["feeds_into_id"] = ["This would create a loop: that team already feeds into this one."]
                    break
                seen.add(cur.pk)
                cur = cur.feeds_into
    amin, amax = values.get("age_min"), values.get("age_max")
    for f, v in (("age_min", amin), ("age_max", amax), ("ordering", values.get("ordering"))):
        if v is not None and not (0 <= v <= 32767):
            errors[f] = ["Must be between 0 and 32767."]
    if amin is not None and amax is not None and amin > amax and "age_max" not in errors:
        errors["age_max"] = ["Must be greater than or equal to the minimum age."]
    for field in ("referee_management", "official_management"):
        if (values.get(field) or Team.Management.CLUB) not in Team.Management.values:
            errors[field] = ["Choose club or federation."]
    if errors:
        raise Invalid(MSG, errors)
    team.name, team.short_name = name, (values.get("short_name") or "").strip() or name
    team.pool, team.feeds_into = pool, feeds
    team.age_min, team.age_max = amin, amax
    team.tournament_only, team.ordering = bool(values.get("tournament_only")), values.get("ordering")
    for field in ("referee_management", "official_management"):
        setattr(team, field, values.get(field) or Team.Management.CLUB)


def _state(team) -> dict:
    return {f: getattr(team, f) for f in FIELDS}


@transaction.atomic
def create_team(actor, values: dict):
    require_settings(actor)
    team = Team()
    _validate(team, values)
    team.save()
    audit.record(actor, "team.created", target=team, changes=audit.snapshot(team, FIELDS))
    return get_team(team.pk)


@transaction.atomic
def update_team(actor, team, values: dict):
    require_team_manager(actor, team)
    before = _state(team)
    if not can_manage_settings(actor):
        # Placing a team in the club's structure is a settings decision; resending the current values is harmless.
        locked = [f for f in STRUCTURE_FIELDS if f in values and values[f] != before[f]]
        if locked:
            raise NotAllowed("Changing the number pool, ages, 'feeds into' or referee management needs the settings permission.")
    merged = {**before, **values}
    _validate(team, merged)
    team.save()
    after = _state(team)
    changes = {k: {"from": audit._json(before[k]), "to": audit._json(after[k])} for k in FIELDS if before[k] != after[k]}
    if changes:
        audit.record(actor, "team.updated", target=team, changes=changes)
    return get_team(team.pk)


@transaction.atomic
def reorder_teams(actor, ids: list) -> None:
    """The listed teams get ordering 1..n in that sequence; teams not listed follow after them in their current order."""
    require_settings(actor)
    if len(set(ids)) != len(ids):
        raise Invalid("Please correct the highlighted fields.", {"ids": ["Each team can only be listed once."]})
    current = list(Team.objects.all())  # model Meta order: ordering first, then name
    by_id = {t.pk: t for t in current}
    if any(i not in by_id for i in ids):
        raise Invalid("Please correct the highlighted fields.", {"ids": ["Unknown team."]})
    ordered = [by_id[i] for i in ids] + [t for t in current if t.pk not in set(ids)]
    changed = {}
    for index, team in enumerate(ordered, start=1):
        if team.ordering != index:
            changed[str(team.pk)] = {"from": team.ordering, "to": index}
            team.ordering = index
            team.save(update_fields=["ordering"])
    if changed:
        audit.record(actor, "team.reordered", changes=changed)


@transaction.atomic
def delete_team(actor, team) -> None:
    require_settings(actor)
    snapshot, pk, label = audit.snapshot(team, FIELDS, deleted=True), team.pk, team.name
    msg = "This team can't be deleted because it has roster or staff history. Remove its players and staff from every season first."
    if team.roster.exists() or team.staff_assignments.exists():
        raise Invalid(msg, {"__all__": [msg]})
    try:
        with transaction.atomic():
            team.delete()
    except ProtectedError, RestrictedError:
        msg = "This team can't be deleted because other records (such as events) still refer to it."
        raise Invalid(msg, {"__all__": [msg]}) from None
    entry = audit.record(actor, "team.deleted", changes=snapshot)
    entry.target_type, entry.target_id, entry.target_label = "team", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


# -- detail ---------------------------------------------------------------------------------------------------


def roster_out(m: TeamMembership) -> dict:
    return {
        "id": m.pk,
        "member_id": m.member_id,
        "first_name": m.member.first_name,
        "last_name": m.member.last_name,
        "photo_url": m.member.photo_url,
        "date_of_birth": m.member.date_of_birth,
        "position_id": m.position_id,
        "position_name": m.position.name if m.position_id else None,
        "jersey_number": m.jersey_number,
        "is_captain": m.is_captain,
        "is_alternate_captain": m.is_alternate_captain,
    }


def staff_out(a: StaffAssignment) -> dict:
    return {
        "id": a.pk,
        "member_id": a.member_id,
        "first_name": a.member.first_name,
        "last_name": a.member.last_name,
        "photo_url": a.member.photo_url,
        "position_id": a.position_id,
        "position_name": a.position.name,
    }


def roster_for(team, season) -> list[TeamMembership]:
    if season is None:
        return []
    return list(TeamMembership.objects.filter(team=team, season=season).select_related("member", "position").order_by(F("position__ordering").asc(nulls_last=True), "position__name", "member__last_name", "member__first_name"))


def photo_url(team, season) -> str | None:
    """The team photo for ``season`` (one per team and season), as a media URL."""
    if season is None:
        return None
    photo = TeamPhoto.objects.filter(team=team, season=season).first()
    return photo.image.url if photo and photo.image else None


@transaction.atomic
def set_photo(actor, team, season, image) -> None:
    """Set (replace) the team photo for ``season``: JPEG, PNG or WebP up to 5 MB, verified by decoding."""
    from PIL import Image, UnidentifiedImageError

    from members.services.members import MAX_PHOTO_BYTES, PHOTO_FORMATS

    require_team_manager(actor, team)
    if image.size > MAX_PHOTO_BYTES:
        raise Invalid("The photo is larger than 5 MB.", {"file": ["The photo is larger than 5 MB."]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"file": ["This is not a valid image."]}) from e
    if fmt not in PHOTO_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP photo.", {"file": ["Use a JPEG, PNG or WebP photo."]})
    image.seek(0)
    photo = TeamPhoto.objects.filter(team=team, season=season).first() or TeamPhoto(team=team, season=season)
    old = photo.image.name if photo.pk and photo.image else ""
    photo.image.save(f"team-{uuid.uuid4().hex[:12]}.{PHOTO_FORMATS[fmt]}", image, save=False)
    photo.save()
    if old:
        photo.image.storage.delete(old)
    audit.record(actor, "team.photo_set", target=team, changes={"photo": {"from": bool(old), "to": True}, "season": {"from": None, "to": season.name}})


@transaction.atomic
def remove_photo(actor, team, season) -> None:
    require_team_manager(actor, team)
    photo = TeamPhoto.objects.filter(team=team, season=season).first()
    if photo is None:
        return
    name = photo.image.name
    storage = photo.image.storage
    photo.delete()
    if name:
        storage.delete(name)
    audit.record(actor, "team.photo_removed", target=team, changes={"photo": {"from": True, "to": None}, "season": {"from": None, "to": season.name}})


def eligible_referees(team) -> list[dict]:
    """Referees whose level (and what it inherits) lets them referee ``team`` and whose qualification is still valid."""
    from teams.models import RefereeProfile

    cache: dict = {}
    out = []
    for p in RefereeProfile.objects.select_related("member", "level").prefetch_related("excluded_teams"):
        if p.level_id is None:
            continue
        if p.level_id not in cache:
            cache[p.level_id] = p.level.eligible_team_ids()
        if team.pk not in p.eligible_team_ids(cache[p.level_id]):
            continue
        m = p.member
        out.append({"member_id": m.pk, "first_name": m.first_name, "last_name": m.last_name, "photo_url": m.photo_url, "level_name": p.level.name, "valid_until": p.valid_until})
    return sorted(out, key=lambda r: (r["last_name"].lower(), r["first_name"].lower()))


def eligible_officials(team) -> list[dict]:
    """Officials (table, timekeeping, ...) whose level (and what it inherits) covers ``team`` and whose qualification is still valid."""
    from teams.models import OfficialProfile

    cache: dict = {}
    out = []
    for p in OfficialProfile.objects.select_related("member", "level"):
        if not p.is_eligible:
            continue
        if p.level_id not in cache:
            cache[p.level_id] = p.level.eligible_team_ids()
        if team.pk not in cache[p.level_id]:
            continue
        m = p.member
        out.append({"member_id": m.pk, "first_name": m.first_name, "last_name": m.last_name, "photo_url": m.photo_url, "level_name": p.level.name, "valid_until": p.valid_until})
    return sorted(out, key=lambda r: (r["last_name"].lower(), r["first_name"].lower()))


def _kind_turnout(team, season, member_ids) -> dict:
    """``{member_id: {"games", "practices"}}``: each player's turnout (percent, None below three answers) on this team's past games
    (games and tournaments) and practices in the season."""
    from django.db.models import Count
    from django.utils import timezone

    from events.models import Attendance, Event
    from events.services import attendance as att

    result = {pk: {"games": None, "practices": None} for pk in member_ids}
    groups = {"games": [Event.EventKind.GAME, Event.EventKind.TOURNAMENT], "practices": [Event.EventKind.TRAINING]}
    for key, kinds in groups.items():
        rows = (
            Attendance.objects.filter(att._in_season(season), member_id__in=list(result), event__teams=team, event__kind__in=kinds, event__cancelled=False, event__start__lt=timezone.now(),
                status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.ABSENT])
            .values("member_id")
            .annotate(present=Count("id", filter=Q(status=Attendance.AttendanceStatus.PRESENT)), responses=Count("id"))
            .order_by()
        )
        for row in rows:
            if row["responses"] >= 3:
                result[row["member_id"]][key] = round(100 * row["present"] / row["responses"])
    return result


def _is_self(actor, member_id) -> bool:
    """True when ``member_id`` is the signed-in person's own member record."""
    return bool(actor is not None and getattr(actor, "is_authenticated", False) and Member.objects.filter(pk=member_id, user=actor).exists())


def _require_not_own_staff_entry(actor, a: StaffAssignment) -> None:
    """A team manager who is not a settings holder can't edit or remove their own staff entry (so a coach can't lock themselves
    out or promote themselves); administrators can."""
    if not access.can_manage_settings(actor) and _is_self(actor, a.member_id):
        raise NotAllowed("You can't change or remove your own staff entry.")


def team_detail(actor, team, season) -> dict:
    staff = []
    if season is not None:
        qs = StaffAssignment.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "position__name", "member__last_name", "member__first_name")
        staff = [staff_out(a) for a in qs]
    roster = [roster_out(m) for m in roster_for(team, season)]
    if season is not None and roster:
        from events.services import attendance as attendance_svc

        rates = {entry["member"].pk: entry["rate"] for entry in attendance_svc.player_attendance_rankings(team, season)}
        kinds = _kind_turnout(team, season, [r["member_id"] for r in roster])
        for r in roster:
            r["attendance_rate"] = rates.get(r["member_id"])
            r["games_rate"] = kinds[r["member_id"]]["games"]
            r["practices_rate"] = kinds[r["member_id"]]["practices"]
    if season is not None and roster and (access.can_manage_team(actor, team) or access.can_manage_members(actor)):
        # Players who cannot be picked for some kind of event because a requirement is still open, with the names of those requirements.
        from members.services import onboarding

        blocked = onboarding.blocking_reasons({(None, season.pk): {r["member_id"] for r in roster}})[(None, season.pk)]
        for r in roster:
            r["open_requirements"] = blocked.get(r["member_id"], [])
    for entry in staff:
        entry["is_self"] = _is_self(actor, entry["member_id"])
    return {**team_out(team), "season_id": season.pk if season else None, "photo_url": photo_url(team, season), "can_manage": access.can_manage_team(actor, team), "roster": roster, "staff": staff}


def roster_pdf_context(team, season, *, now=None) -> dict:
    """What the roster PDF prints: the club and team header, the players (shirt number, name, position, captain marks) and the staff
    (name, position) of ``season``. Names and roles only: no contact details, birth dates or photos."""
    from django.utils import timezone

    from events.services.lineup import current_club

    players = [
        {
            "number": m.jersey_number,
            "name": m.member.get_full_name(),
            "position": m.position.name if m.position_id else "",
            "captain": m.is_captain,
            "alternate": m.is_alternate_captain,
        }
        for m in roster_for(team, season)
    ]
    staff = []
    if season is not None:
        assignments = StaffAssignment.objects.filter(team=team, season=season).select_related("member", "position").order_by("position__ordering", "position__name", "member__last_name", "member__first_name")
        staff = [{"name": a.member.get_full_name(), "position": a.position.name} for a in assignments]
    return {"club": current_club(), "team": team, "season": season, "players": players, "staff": staff, "generated_at": now or timezone.localtime()}


def render_roster_pdf(team, season) -> bytes:
    """The roster as a PDF (WeasyPrint). Raises ``shop.services.invoices.PDFUnavailable`` when it cannot render here."""
    from django.template.loader import render_to_string

    from shop.services import invoices

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise invoices.PDFUnavailable("PDF rendering is not available on this server.") from error
    context = roster_pdf_context(team, season)
    context["logo_data_uri"] = invoices._logo_uri(context["club"])
    return HTML(string=render_to_string("teams/roster.html", context)).write_pdf()


# -- roster ---------------------------------------------------------------------------------------------------

ROSTER_FIELDS = ("position_id", "jersey_number", "is_captain", "is_alternate_captain")


def get_membership(team, membership_id):
    return TeamMembership.objects.select_related("member", "position", "season").filter(pk=membership_id, team=team).first()


def get_assignment(team, assignment_id):
    return StaffAssignment.objects.select_related("member", "position", "season").filter(pk=assignment_id, team=team).first()


def _position(position_id, *, staff: bool, field="position_id"):
    if position_id is None:
        return None
    pos = Position.objects.filter(pk=position_id).first()
    if pos is None:
        _fail(field, "Unknown position.")
    if pos.staff_position != staff:
        _fail(field, "Choose a staff position." if staff else "Choose a player position, not a staff position.")
    return pos


def _check_number(team, season, member, number, exclude_pk=None):
    if number is None:
        return
    msg = numbers.range_message(number, team) if number >= 0 else "Must be zero or more."
    if msg is None:
        msg = numbers.clash_message(number, season, team, member, exclude_pk)
    if msg:
        _fail("jersey_number", msg)


def _apply_captaincy(m: TeamMembership):
    if m.is_captain:
        m.is_alternate_captain = False
        TeamMembership.objects.filter(team=m.team, season=m.season, is_captain=True).exclude(pk=m.pk).update(is_captain=False)


def _roster_state(m) -> dict:
    return {"team": m.team.name, "season": m.season.name, "position": m.position.name if m.position_id else None, "jersey_number": m.jersey_number, "is_captain": m.is_captain, "is_alternate_captain": m.is_alternate_captain}


def _require_active_membership(member, season) -> None:
    """Only someone with an *active* (paid up) membership in the season goes on a roster or a staff."""
    from members.models import ClubMembership

    if not ClubMembership.objects.filter(member=member, season=season, status=ClubMembership.StatusChoices.ACTIVE).exists():
        _fail("member_id", "Only members with an active (paid up) membership this season can be added.")


@transaction.atomic
def add_player(actor, team, *, member_id, season_id, position_id, jersey_number, is_captain, is_alternate_captain, check_membership=True):
    """``check_membership=False``: the caller (the member import) has just made the membership and it is still pending."""
    require_team_manager(actor, team)
    season = resolve_season(season_id)
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        _fail("member_id", "Unknown member.")
    if check_membership:
        _require_active_membership(member, season)
    if TeamMembership.objects.filter(team=team, season=season, member=member).exists():
        _fail("member_id", f"{member} is already on this team in {season.name}.")
    position = _position(position_id, staff=False)
    _check_number(team, season, member, jersey_number)
    m = TeamMembership(team=team, member=member, season=season, position=position, jersey_number=jersey_number, is_captain=is_captain, is_alternate_captain=is_alternate_captain and not is_captain)
    m.save()
    _apply_captaincy(m)
    audit.record(actor, "team.roster_added", member=member, target=m, target_label=f"{member} - {team.name}", changes={k: {"from": None, "to": v} for k, v in _roster_state(m).items()})
    return get_membership(team, m.pk)


@transaction.atomic
def update_player(actor, m: TeamMembership, values: dict):
    require_team_manager(actor, m.team)
    before = _roster_state(m)
    if "position_id" in values:
        m.position = _position(values["position_id"], staff=False)
    if "jersey_number" in values:
        _check_number(m.team, m.season, m.member, values["jersey_number"], exclude_pk=m.pk)
        m.jersey_number = values["jersey_number"]
    for f in ("is_captain", "is_alternate_captain"):
        if values.get(f) is not None:
            setattr(m, f, values[f])
    if values.get("is_captain") and "is_alternate_captain" not in values:
        m.is_alternate_captain = False
    elif values.get("is_alternate_captain") and "is_captain" not in values:
        m.is_captain = False
    elif m.is_captain and m.is_alternate_captain:
        _fail("is_alternate_captain", "A captain can't also be alternate captain.")
    m.save()
    _apply_captaincy(m)
    after = _roster_state(m)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "team.roster_updated", member=m.member, target=m, target_label=f"{m.member} - {m.team.name}", changes=changes)
    return get_membership(m.team, m.pk)


@transaction.atomic
def remove_player(actor, m: TeamMembership) -> None:
    require_team_manager(actor, m.team)
    state, label, member = _roster_state(m), f"{m.member} - {m.team.name}", m.member
    pk = m.pk
    m.delete()
    entry = audit.record(actor, "team.roster_removed", member=member, changes={k: {"from": v, "to": None} for k, v in state.items()})
    entry.target_type, entry.target_id, entry.target_label = "teammembership", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


# -- staff ----------------------------------------------------------------------------------------------------


def _staff_state(a) -> dict:
    return {"team": a.team.name, "season": a.season.name, "position": a.position.name}


@transaction.atomic
def add_staff(actor, team, *, member_id, season_id, position_id):
    require_team_manager(actor, team)
    season = resolve_season(season_id)
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        _fail("member_id", "Unknown member.")
    _require_active_membership(member, season)
    if StaffAssignment.objects.filter(team=team, season=season, member=member).exists():
        _fail("member_id", f"{member} is already on this team's staff in {season.name}.")
    position = _position(position_id, staff=True)
    if position is None:
        _fail("position_id", "This field is required.")
    a = StaffAssignment.objects.create(team=team, member=member, season=season, position=position)
    audit.record(actor, "team.staff_added", member=member, target=a, target_label=f"{member} - {team.name}", changes={k: {"from": None, "to": v} for k, v in _staff_state(a).items()})
    return get_assignment(team, a.pk)


MAX_BULK = 100


def add_people(actor, team, *, season_id, rows: list[dict]) -> dict:
    """Add several people to the team in one go. Each row is ``{member_id, position_id, jersey_number, is_captain,
    is_alternate_captain}``; the position decides the role: a staff position puts the person on the staff, any other
    on the roster (the number and captain flags only apply to players). Only members with an *active* membership in
    the season can be added. A row that fails (already on the team, not active, ...) is reported with its reason and
    the others still go in. Returns ``{"added": n, "rows": [{index, ok, role, error}]}``."""
    from members.models import ClubMembership

    require_team_manager(actor, team)
    if len(rows) > MAX_BULK:
        raise Invalid(f"At most {MAX_BULK} people at once.", {"rows": [f"At most {MAX_BULK} people at once."]})
    season = resolve_season(season_id)
    positions = {p.pk: p for p in Position.objects.all()}
    results, added = [], 0
    for index, row in enumerate(rows):
        result = {"index": index, "ok": False, "role": None, "error": None}
        position = positions.get(row.get("position_id"))
        try:
            with transaction.atomic():
                if position is None:
                    _fail("position_id", "Choose a position.")
                if not ClubMembership.objects.filter(member_id=row["member_id"], season=season, status=ClubMembership.StatusChoices.ACTIVE).exists():
                    _fail("member_id", "Only members with an active (paid up) membership this season can be added.")
                if position.staff_position:
                    add_staff(actor, team, member_id=row["member_id"], season_id=season.pk, position_id=position.pk)
                    result["role"] = "staff"
                else:
                    add_player(
                        actor,
                        team,
                        member_id=row["member_id"],
                        season_id=season.pk,
                        position_id=position.pk,
                        jersey_number=row.get("jersey_number"),
                        is_captain=bool(row.get("is_captain")),
                        is_alternate_captain=bool(row.get("is_alternate_captain")),
                    )
                    result["role"] = "player"
            result["ok"] = True
            added += 1
        except Invalid as e:
            result["error"] = next(itertools.chain.from_iterable((e.errors or {}).values()), None) or str(e)
        results.append(result)
    return {"added": added, "rows": results}


@transaction.atomic
def update_staff(actor, a: StaffAssignment, *, position_id):
    require_team_manager(actor, a.team)
    _require_not_own_staff_entry(actor, a)
    before = _staff_state(a)
    position = _position(position_id, staff=True)
    if position is None:
        _fail("position_id", "This field is required.")
    a.position = position
    a.save()
    after = _staff_state(a)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "team.staff_updated", member=a.member, target=a, target_label=f"{a.member} - {a.team.name}", changes=changes)
    return get_assignment(a.team, a.pk)


@transaction.atomic
def remove_staff(actor, a: StaffAssignment) -> None:
    require_team_manager(actor, a.team)
    _require_not_own_staff_entry(actor, a)
    state, label, member, pk = _staff_state(a), f"{a.member} - {a.team.name}", a.member, a.pk
    a.delete()
    entry = audit.record(actor, "team.staff_removed", member=member, changes={k: {"from": v, "to": None} for k, v in state.items()})
    entry.target_type, entry.target_id, entry.target_label = "staffassignment", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


# -- numbers --------------------------------------------------------------------------------------------------


def _holder(m: TeamMembership) -> dict:
    return {"member_id": m.member_id, "first_name": m.member.first_name, "last_name": m.member.last_name, "date_of_birth": m.member.date_of_birth, "team_id": m.team_id, "team_name": m.team.name}


def _pool_out(p) -> dict:
    return {"id": p.pk, "name": p.name, "min_number": p.min_number, "max_number": p.max_number, "min_age_gap_years": p.min_age_gap_years}


def _pending_holds(season, pool=None) -> dict[int, list]:
    """Numbers held by registrations that still wait for the billing review, as unsaved roster spots (``{number: [spots]}``)."""
    if season is None:
        return {}
    from registration.services import holds

    return {n: [holds._probe(line) for line in lines] for n, lines in holds.pending_by_number(season, pool=pool).items()}


def _number_row(n, holders, pending, res, pool, actor) -> dict:
    """One number of the overview. Held by pending registrations only (nobody on a roster, no reservation) it reads ``held``."""
    status = numbers.status_for([*holders, *pending], res, pool)
    if pending and not holders and res is None and status in ("taken", "shared"):
        status = "held"
    return {"number": n, "status": status, "holders": [*(_holder(m) for m in holders), *({**_holder(m), "pending": True} for m in pending)], "reservation": numbers.reservation_info(res, actor) if res else None}


def numbers_overview(actor, season, pool_id=None) -> list[dict]:
    pools = NumberPool.objects.order_by("name")
    if pool_id is not None:
        pools = pools.filter(pk=pool_id)
    out = []
    for pool in pools:
        memberships = list(numbers.memberships_for(season, pool=pool).order_by("jersey_number", "member__last_name", "member__first_name")) if season else []
        by_number: dict[int, list] = {}
        for m in memberships:
            by_number.setdefault(m.jersey_number, []).append(m)
        reservations = {r.number: r for r in NumberReservation.objects.filter(pool=pool).select_related("reserved_by")}
        pending = _pending_holds(season, pool=pool)
        wanted = sorted(set(range(pool.min_number, pool.max_number + 1)) | set(by_number) | set(reservations) | set(pending))
        rows = []
        for n in wanted:
            holders, res = by_number.get(n, []), reservations.get(n)
            rows.append(_number_row(n, holders, pending.get(n, []), res, pool, actor))
        out.append({"pool": _pool_out(pool), "teams": [{"id": t.pk, "name": t.name} for t in pool.teams.all()], "numbers": rows})
    if pool_id is None:
        loose = list(Team.objects.filter(pool__isnull=True))
        memberships = list(TeamMembership.objects.filter(season=season, team__pool__isnull=True, jersey_number__isnull=False).select_related("member", "team").order_by("jersey_number", "member__last_name")) if season else []
        by_number = {}
        for m in memberships:
            by_number.setdefault(m.jersey_number, []).append(m)
        if loose:
            pending = _pending_holds(season, pool=None)
            rows = [_number_row(n, by_number.get(n, []), pending.get(n, []), None, None, actor) for n in sorted(set(by_number) | set(pending))]
            out.append({"pool": None, "teams": [{"id": t.pk, "name": t.name} for t in loose], "numbers": rows})
    return out


def team_numbers(actor, team, season) -> dict:
    pool = team.pool
    players = [roster_out(m) for m in roster_for(team, season) if m.jersey_number is not None]
    own = {p["jersey_number"] for p in players}
    taken, reserved = {}, {}
    if season is not None and pool is not None:
        for m in numbers.memberships_for(season, pool=pool).exclude(team=team).order_by("member__last_name", "member__first_name"):
            taken.setdefault(str(m.jersey_number), []).append(_holder(m))
        for n, spots in _pending_holds(season, pool=pool).items():  # held by registrations waiting for review (read-only, flagged pending)
            for m in spots:
                if m.team_id != team.pk:
                    taken.setdefault(str(n), []).append({**_holder(m), "pending": True})
        reserved = {str(r.number): r.note for r in NumberReservation.objects.filter(pool=pool)}
    free = []
    if pool is not None:
        free = [n for n in range(pool.min_number, pool.max_number + 1) if n not in own and str(n) not in taken and str(n) not in reserved]
    return {"season_id": season.pk if season else None, "pool": _pool_out(pool) if pool else None, "players": players, "free_numbers": free, "taken_by_others": taken, "reserved": reserved}
