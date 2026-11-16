"""Team settings: positions, number pools, number reservations and the club-wide referee / official overview.
Everyone reads; changing positions and pools needs the settings permission; audit is club-level (no member), like the officiating levels."""

from django.db import transaction
from django.db.models import Q

from events.models import Season
from members.services import audit
from members.services.access import can_manage_any_team, require_settings
from members.services.errors import Invalid, NotAllowed
from teams.models import NumberPool, NumberReservation, OfficialProfile, Position, RefereeProfile, Team, TeamMembership


def _invalid(errors: dict, message="Please correct the highlighted fields."):
    raise Invalid(message, errors)


def _finish_delete(actor, action, instance, snapshot):
    pk, label, model = instance.pk, str(instance), instance._meta.model_name
    instance.delete()
    entry = audit.record(actor, action, changes={k: {"from": v, "to": None} for k, v in snapshot.items()})
    entry.target_type, entry.target_id, entry.target_label = model, str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


# -- positions --

POSITION_FIELDS = ("name", "short_name", "ordering", "staff_position", "management_position")


def position_usage(position) -> tuple[int, int]:
    return position.team_memberships.count(), position.staff_assignments.count()


def position_out(position) -> dict:
    roster, staff = position_usage(position)
    return {**{f: getattr(position, f) for f in POSITION_FIELDS}, "id": position.pk, "in_use_count": roster + staff}


def list_positions(actor) -> list[dict]:
    return [position_out(p) for p in Position.objects.all()]


def get_position(position_id):
    return Position.objects.filter(pk=position_id).first()


def _apply_position(position, values: dict):
    for k, v in values.items():
        if v is not None:
            setattr(position, k, v)
    errors = {}
    position.name = (position.name or "").strip()
    position.short_name = (position.short_name or "").strip() or position.name[:255]
    if not position.name:
        errors["name"] = ["This field is required."]
    if position.ordering is None or position.ordering < 0:
        errors["ordering"] = ["Must be zero or greater."]
    if position.management_position and not position.staff_position:
        errors["management_position"] = ["A management position must also be a staff position."]
    if errors:
        _invalid(errors)


def _position_state(p):
    return {f: getattr(p, f) for f in POSITION_FIELDS}


@transaction.atomic
def create_position(actor, **values):
    require_settings(actor)
    position = Position()
    _apply_position(position, {**dict.fromkeys(POSITION_FIELDS), **values})
    position.save()
    audit.record(actor, "club.position_created", target=position, changes=audit.snapshot(position, POSITION_FIELDS))
    return position


@transaction.atomic
def update_position(actor, position, **values):
    require_settings(actor)
    before = _position_state(position)
    _apply_position(position, values)
    roster, staff = position_usage(position)
    if position.staff_position != before["staff_position"] and (roster if before["staff_position"] is False else staff):
        _invalid({"staff_position": ["This position is in use, so it can't switch between roster and staff."]})
    position.save()
    after = _position_state(position)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "club.position_updated", target=position, changes=changes)
    return position


@transaction.atomic
def reorder_positions(actor, ids: list):
    """Persist a new order: the listed positions get ordering 1..n in that sequence, any not listed follow after them
    (keeping their relative order). Unknown or repeated ids are a 422."""
    require_settings(actor)
    if len(set(ids)) != len(ids):
        _invalid({"ids": ["Each position can only be listed once."]})
    by_id = {p.pk: p for p in Position.objects.all()}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        _invalid({"ids": ["Unknown position."]})
    ordered = [by_id[i] for i in ids] + [p for p in Position.objects.all() if p.pk not in set(ids)]
    changed = {}
    for index, position in enumerate(ordered, start=1):
        if position.ordering != index:
            changed[str(position.pk)] = {"from": position.ordering, "to": index}
            position.ordering = index
            position.save(update_fields=["ordering"])
    if changed:
        audit.record(actor, "club.positions_reordered", changes=changed)
    return list_positions(actor)


@transaction.atomic
def delete_position(actor, position):
    require_settings(actor)
    roster, staff = position_usage(position)
    if roster or staff:
        parts = []
        if roster:
            parts.append(f"{roster} roster spot{'s' if roster != 1 else ''}")
        if staff:
            parts.append(f"{staff} staff assignment{'s' if staff != 1 else ''}")
        msg = f"This position can't be deleted: it is used by {' and '.join(parts)}."
        _invalid({"__all__": [msg]}, msg)
    _finish_delete(actor, "club.position_deleted", position, _position_state(position))


# -- number pools --

POOL_FIELDS = ("name", "min_number", "max_number", "min_age_gap_years")


def pool_out(pool) -> dict:
    teams = list(pool.teams.order_by("name"))
    return {
        **{f: getattr(pool, f) for f in POOL_FIELDS},
        "id": pool.pk,
        "team_ids": [t.pk for t in teams],
        "teams": [{"id": t.pk, "name": t.name} for t in teams],
        "reservation_count": pool.reservations.count(),
    }


def list_pools(actor) -> list[dict]:
    return [pool_out(p) for p in NumberPool.objects.all()]


def get_pool(pool_id):
    return NumberPool.objects.filter(pk=pool_id).first()


def _apply_pool(pool, values: dict, team_ids):
    for k, v in values.items():
        if v is not None:
            setattr(pool, k, v)
    errors = {}
    pool.name = (pool.name or "").strip()
    if not pool.name:
        errors["name"] = ["This field is required."]
    for f in ("min_number", "max_number", "min_age_gap_years"):
        v = getattr(pool, f)
        if v is None or v < 0 or v > 32767:
            errors[f] = ["Must be a whole number between 0 and 32767."]
    if not errors and pool.min_number > pool.max_number:
        errors["max_number"] = ["Must be greater than or equal to the minimum number."]
    teams = None
    if team_ids is not None:
        wanted = set(team_ids)
        teams = list(Team.objects.filter(pk__in=wanted))
        if len(teams) != len(wanted):
            errors["team_ids"] = ["Unknown team."]
    if not errors and pool.pk:
        outside = pool.reservations.filter(Q(number__lt=pool.min_number) | Q(number__gt=pool.max_number)).order_by("number").values_list("number", flat=True)
        if outside:
            errors["max_number"] = [f"Reserved number{'s' if len(outside) > 1 else ''} {', '.join(f'#{n}' for n in outside)} would fall outside this range; release them first."]
    if errors:
        _invalid(errors)
    return teams


def _pool_state(pool):
    return {**{f: getattr(pool, f) for f in POOL_FIELDS}, "teams": sorted(t.name for t in pool.teams.all())}


def _set_teams(pool, teams):
    if teams is None:
        return
    keep = {t.pk for t in teams}
    Team.objects.filter(pool=pool).exclude(pk__in=keep).update(pool=None)
    Team.objects.filter(pk__in=keep).update(pool=pool)


@transaction.atomic
def create_pool(actor, *, team_ids=None, **values):
    require_settings(actor)
    pool = NumberPool()
    teams = _apply_pool(pool, {**dict.fromkeys(POOL_FIELDS), **values}, team_ids or [])
    pool.save()
    _set_teams(pool, teams)
    state = _pool_state(pool)
    audit.record(actor, "club.number_pool_created", target=pool, changes={k: {"from": None, "to": v} for k, v in state.items()})
    return pool


@transaction.atomic
def update_pool(actor, pool, *, team_ids=None, **values):
    require_settings(actor)
    before = _pool_state(pool)
    teams = _apply_pool(pool, values, team_ids)
    pool.save()
    _set_teams(pool, teams)
    after = _pool_state(pool)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "club.number_pool_updated", target=pool, changes=changes)
    return pool


@transaction.atomic
def delete_pool(actor, pool):
    require_settings(actor)
    _finish_delete(actor, "club.number_pool_deleted", pool, _pool_state(pool))


# -- reservations --


def reservation_out(r, actor=None) -> dict:
    from teams.services import numbers

    return {"id": r.pk, "number": r.number, "note": r.note, "reserved_by_name": numbers.reserver_name(r), "can_release": numbers.can_release(actor, r)}


def list_reservations(actor, pool) -> list[dict]:
    return [reservation_out(r, actor) for r in pool.reservations.select_related("reserved_by")]


def get_reservation(pool, reservation_id):
    return pool.reservations.filter(pk=reservation_id).first()


@transaction.atomic
def create_reservation(actor, pool, *, number: int, note: str = ""):
    if not can_manage_any_team(actor):
        raise NotAllowed("You are not allowed to reserve numbers.")
    if not (pool.min_number <= number <= pool.max_number):
        _invalid({"number": [f"Must be within the pool's range ({pool.min_number}-{pool.max_number})."]})
    if pool.reservations.filter(number=number).exists():
        _invalid({"number": ["This number is already reserved."]})
    season = Season.get_current()
    if season is not None:
        holders = TeamMembership.objects.filter(season=season, jersey_number=number, team__pool=pool).select_related("member", "team")
        names = sorted({f"{h.member.first_name} {h.member.last_name} ({h.team.name})" for h in holders})
        if names:
            _invalid({"number": [f"#{number} is currently worn by {', '.join(names)}."]})
    reservation = NumberReservation.objects.create(pool=pool, number=number, note=(note or "").strip(), reserved_by=actor if getattr(actor, "pk", None) else None)
    audit.record(actor, "club.number_reservation_created", target=reservation, changes={"pool": {"from": None, "to": pool.name}, "number": {"from": None, "to": number}, "note": {"from": None, "to": reservation.note}})
    return reservation


@transaction.atomic
def delete_reservation(actor, reservation):
    from teams.services import numbers

    if not numbers.can_release(actor, reservation):
        raise NotAllowed("Only a settings admin or the person who made the reservation can release it.")
    snapshot = {"pool": reservation.pool.name, "number": reservation.number, "note": reservation.note}
    _finish_delete(actor, "club.number_reservation_deleted", reservation, snapshot)


# -- officiating overview --


def officiating_people(actor, *, kind="all", q="", level=None, valid="all") -> list[dict]:
    team_names = dict(Team.objects.values_list("pk", "name"))
    cache: dict = {}

    def level_teams(lv):
        if lv.pk not in cache:
            cache[lv.pk] = lv.eligible_team_ids()
        return cache[lv.pk]

    own_cache: dict = {}

    def own_teams(lv):
        if lv.pk not in own_cache:
            own_cache[lv.pk] = set(lv.teams.values_list("id", flat=True))
        return own_cache[lv.pk]

    rows = []
    sources = []
    if kind in ("referee", "all"):
        sources.append(("referee", RefereeProfile.objects.select_related("member", "level").prefetch_related("excluded_teams")))
    if kind in ("official", "all"):
        sources.append(("official", OfficialProfile.objects.select_related("member", "level")))
    q = (q or "").strip()
    for k, qs in sources:
        if q:
            for term in q.split():
                qs = qs.filter(Q(member__first_name__icontains=term) | Q(member__last_name__icontains=term))
        if level is not None:
            qs = qs.filter(level_id=level)
        for p in qs:
            is_valid = p.is_eligible
            if valid == "valid" and not is_valid:
                continue
            if valid == "expired" and is_valid:
                continue
            excluded_teams = sorted(p.excluded_teams.all(), key=lambda t: t.name) if k == "referee" else []
            excluded = [t.name for t in excluded_teams]
            if k == "referee":
                ids = p.eligible_team_ids(level_teams(p.level) if p.level_id else None)
            else:
                ids = level_teams(p.level) if is_valid else set()
            # Teams that only come through an inherited level (not set on the person's own level).
            inherited = (ids - own_teams(p.level)) if p.level_id else set()
            m = p.member
            rows.append(
                {
                    "member_id": m.pk,
                    "first_name": m.first_name,
                    "last_name": m.last_name,
                    "photo_url": m.photo_url,
                    "kind": k,
                    "level_id": p.level_id,
                    "level_name": p.level.name if p.level_id else None,
                    "valid_until": p.valid_until,
                    "is_valid": is_valid,
                    "eligible_team_names": sorted(team_names[i] for i in ids if i in team_names),
                    "inherited_team_names": sorted(team_names[i] for i in inherited if i in team_names),
                    "excluded_team_names": excluded,
                    "excluded_team_ids": [t.pk for t in excluded_teams],
                }
            )
    rows.sort(key=lambda r: (r["last_name"].lower(), r["first_name"].lower(), r["kind"]))
    return rows
