"""Locations and opponents for the calendar. Everyone who may use the management app reads them; changing them needs
the settings permission or a management position on any team (the people who schedule events). Writes are audited
at club level. Deleting one that events use is allowed: those events simply lose it (the foreign keys are SET_NULL),
and the counts say how many are affected. The home location is set from the control panel and cannot be deleted here."""

import uuid

from django.db import transaction
from django.db.models import Q
from django_countries import countries

from members.services import audit
from members.services.access import can_manage_any_team
from members.services.errors import Invalid, NotAllowed
from members.services.members import MAX_PHOTO_BYTES, PHOTO_FORMATS

from .models import Location, Opponent

MSG = "Please correct the highlighted fields."
LOCATION_FIELDS = ("name", "address", "city", "zip_code", "country", "latitude", "longitude")
OPPONENT_FIELDS = ("name", "alternative_names")


def require_calendar_manager(actor) -> None:
    if not can_manage_any_team(actor):
        raise NotAllowed("You are not allowed to change locations and opponents.")


def country_choices() -> list[dict]:
    return [{"code": c.code, "name": str(c.name)} for c in countries]


# -- locations --


def location_out(loc: Location) -> dict:
    return {
        "id": loc.pk,
        "name": loc.name,
        "address": loc.address,
        "city": loc.city,
        "zip_code": loc.zip_code,
        "country": str(loc.country),
        "country_name": loc.country.name if loc.country else "",
        "is_home": loc.is_home,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "event_count": loc.events.count() + loc.event_series.count(),
    }


def list_locations(q: str = "") -> list[dict]:
    qs = Location.objects.all()
    for word in (q or "").split():
        qs = qs.filter(Q(name__icontains=word) | Q(city__icontains=word) | Q(address__icontains=word) | Q(zip_code__icontains=word))
    return [location_out(loc) for loc in qs.order_by("name")]


def get_location(location_id):
    return Location.objects.filter(pk=location_id).first()


def _clean_location(loc: Location, values: dict) -> None:
    errors = {}
    merged = {f: values.get(f, getattr(loc, f)) for f in LOCATION_FIELDS}
    for f in ("name", "address", "city", "zip_code"):
        merged[f] = (merged[f] or "").strip()
        if not merged[f]:
            errors[f] = ["This field is required."]
    code = str(merged["country"] or "").strip().upper()
    if code not in {c.code for c in countries}:
        errors["country"] = ["Choose a country."]
    elif Location.objects.filter(name__iexact=merged["name"]).exclude(pk=loc.pk).exists():
        errors["name"] = ["A location with this name already exists."]
    lat, lng = merged["latitude"], merged["longitude"]
    if (lat is None) != (lng is None):
        errors["latitude" if lat is None else "longitude"] = ["Give both latitude and longitude, or neither."]
    if lat is not None and not -90 <= lat <= 90:
        errors["latitude"] = ["Must be between -90 and 90."]
    if lng is not None and not -180 <= lng <= 180:
        errors["longitude"] = ["Must be between -180 and 180."]
    if errors:
        raise Invalid(MSG, errors)
    merged["country"] = code
    for f, v in merged.items():
        setattr(loc, f, v)


def _state(obj, fields) -> dict:
    return {f: (str(getattr(obj, f)) if f == "country" else getattr(obj, f)) for f in fields}


@transaction.atomic
def create_location(actor, **values) -> Location:
    require_calendar_manager(actor)
    loc = Location(is_home=False)
    _clean_location(loc, values)
    loc.save()
    audit.record(actor, "club.location_created", target=loc, changes={k: {"from": None, "to": v} for k, v in _state(loc, LOCATION_FIELDS).items()})
    return loc


@transaction.atomic
def update_location(actor, loc: Location, **values) -> Location:
    require_calendar_manager(actor)
    before = _state(loc, LOCATION_FIELDS)
    _clean_location(loc, values)
    loc.save()
    after = _state(loc, LOCATION_FIELDS)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "club.location_updated", target=loc, changes=changes)
    return loc


def _record_delete(actor, action, obj, snapshot):
    pk, label, model = obj.pk, str(obj), obj._meta.model_name
    obj.delete()
    entry = audit.record(actor, action, changes={k: {"from": v, "to": None} for k, v in snapshot.items()})
    entry.target_type, entry.target_id, entry.target_label = model, str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def delete_location(actor, loc: Location) -> None:
    require_calendar_manager(actor)
    if loc.is_home:
        raise Invalid("The home location is managed from the control panel and can't be deleted here.", {"__all__": ["The home location can't be deleted."]})
    _record_delete(actor, "club.location_deleted", loc, _state(loc, LOCATION_FIELDS))


# -- opponents --


def opponent_out(o: Opponent) -> dict:
    return {
        "id": o.pk,
        "name": o.name,
        "logo_url": o.logo.url if o.logo else None,
        "alternative_names": list(o.alternative_names),
        "event_count": o.events.count() + o.event_series.count(),
    }


def list_opponents(q: str = "") -> list[dict]:
    qs = Opponent.objects.all().order_by("name")
    words = (q or "").split()
    rows = [o for o in qs if all(w.casefold() in " ".join([o.name, *o.alternative_names]).casefold() for w in words)]
    return [opponent_out(o) for o in rows]


def get_opponent(opponent_id):
    return Opponent.objects.filter(pk=opponent_id).first()


def _clean_opponent(o: Opponent, values: dict) -> None:
    errors = {}
    name = (values.get("name", o.name) or "").strip()
    alts = values.get("alternative_names", list(o.alternative_names))
    if not name:
        errors["name"] = ["This field is required."]
    others = [x for x in Opponent.objects.exclude(pk=o.pk)]
    taken = {k.casefold(): x for x in others for k in [x.name, *x.alternative_names]}
    if name and name.casefold() in taken:
        errors["name"] = [f"This name is already used by {taken[name.casefold()].name}."]
    clean, seen = [], {name.casefold()}
    for a in alts or []:
        a = str(a).strip()
        if not a or a.casefold() in seen:
            continue
        if a.casefold() in taken:
            errors["alternative_names"] = [f"'{a}' is already used by {taken[a.casefold()].name}."]
        seen.add(a.casefold())
        clean.append(a)
    if errors:
        raise Invalid(MSG, errors)
    o.name, o.alternative_names = name, clean


@transaction.atomic
def create_opponent(actor, **values) -> Opponent:
    require_calendar_manager(actor)
    o = Opponent()
    _clean_opponent(o, values)
    o.save()
    audit.record(actor, "club.opponent_created", target=o, changes={k: {"from": None, "to": v} for k, v in _state(o, OPPONENT_FIELDS).items()})
    return o


@transaction.atomic
def update_opponent(actor, o: Opponent, **values) -> Opponent:
    require_calendar_manager(actor)
    before = _state(o, OPPONENT_FIELDS)
    _clean_opponent(o, values)
    o.save()
    after = _state(o, OPPONENT_FIELDS)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        audit.record(actor, "club.opponent_updated", target=o, changes=changes)
    return o


@transaction.atomic
def delete_opponent(actor, o: Opponent) -> None:
    require_calendar_manager(actor)
    name = o.logo.name if o.logo else ""
    storage = o.logo.storage if o.logo else None
    _record_delete(actor, "club.opponent_deleted", o, _state(o, OPPONENT_FIELDS))
    if name and storage:
        storage.delete(name)


@transaction.atomic
def set_opponent_logo(actor, o: Opponent, image) -> Opponent:
    """JPEG, PNG or WebP up to 5 MB, verified by decoding."""
    from PIL import Image, UnidentifiedImageError

    require_calendar_manager(actor)
    if image.size > MAX_PHOTO_BYTES:
        raise Invalid("The logo is larger than 5 MB.", {"file": ["The logo is larger than 5 MB."]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"file": ["This is not a valid image."]}) from e
    if fmt not in PHOTO_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP logo.", {"file": ["Use a JPEG, PNG or WebP logo."]})
    image.seek(0)
    old = o.logo.name if o.logo else ""
    o.logo.save(f"opponent-{uuid.uuid4().hex[:12]}.{PHOTO_FORMATS[fmt]}", image, save=False)
    o.save(update_fields=["logo"])
    if old:
        o.logo.storage.delete(old)
    audit.record(actor, "club.opponent_logo_changed", target=o, changes={"logo": {"from": bool(old), "to": True}})
    return o


@transaction.atomic
def remove_opponent_logo(actor, o: Opponent) -> Opponent:
    require_calendar_manager(actor)
    if o.logo:
        name, storage = o.logo.name, o.logo.storage
        o.logo = ""
        o.save(update_fields=["logo"])
        storage.delete(name)
        audit.record(actor, "club.opponent_logo_changed", target=o, changes={"logo": {"from": True, "to": None}})
    return o
