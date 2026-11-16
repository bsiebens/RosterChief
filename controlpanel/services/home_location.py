"""A club's home location: the events.Location flagged ``is_home`` in its schema, so setting it here edits the very row the club's own
location list shows. At most one location is home; ``set_home_location`` keeps it that way (the model has no database constraint)."""

from django.db import transaction
from django_countries import countries
from django_tenants.utils import schema_context

from events.models import Location

FIELDS = ("name", "address", "zip_code", "city", "country")
OPTIONAL = ("latitude", "longitude")


class HomeLocationError(Exception):
    def __init__(self, message: str, fields: dict[str, list[str]] | None = None):
        super().__init__(message)
        self.fields = fields or {}


def location_data(location: Location | None) -> dict | None:
    if location is None:
        return None
    return {
        "id": location.pk,
        "name": location.name,
        "address": location.address,
        "zip_code": location.zip_code,
        "city": location.city,
        "country": location.country.code or "",
        "country_name": str(location.country.name) if location.country else "",
        "latitude": location.latitude,
        "longitude": location.longitude,
    }


def get_home_location(club) -> dict | None:
    with schema_context(club.schema_name):
        return location_data(Location.objects.filter(is_home=True).order_by("created").first())


def clean(values: dict) -> dict:
    """Trim everything, require every field and check the country code. Raises ``HomeLocationError`` with per-field messages."""
    cleaned: dict = {f: str(values.get(f) or "").strip() for f in FIELDS}
    errors = {f: ["This field is required."] for f, v in cleaned.items() if not v}
    if cleaned["country"]:
        cleaned["country"] = cleaned["country"].upper()
        if cleaned["country"] not in dict(countries):
            errors["country"] = ["Use a two-letter country code such as BE."]
    for f, limit in (("latitude", 90), ("longitude", 180)):
        raw = values.get(f)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            cleaned[f] = None
            continue
        try:
            number = float(raw)
        except TypeError, ValueError:
            errors[f] = ["Enter a number."]
            continue
        if not -limit <= number <= limit:
            errors[f] = [f"Must be between -{limit} and {limit}."]
        cleaned[f] = round(number, 6)
    if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
        errors.setdefault("longitude" if cleaned.get("longitude") is None else "latitude", ["Give both latitude and longitude, or neither."])
    if errors:
        raise HomeLocationError("The location is not valid.", errors)
    return cleaned


@transaction.atomic
def set_home_location(club, values: dict) -> tuple[dict, bool]:
    """Create the home location, or update the existing one. Returns ``(data, created)``."""
    cleaned = clean(values)
    with schema_context(club.schema_name):
        existing = list(Location.objects.select_for_update().filter(is_home=True).order_by("created"))
        location = existing[0] if existing else Location(is_home=True)
        for field, value in cleaned.items():
            setattr(location, field, value)
        location.is_home = True
        location.full_clean()
        location.save()
        Location.objects.filter(is_home=True).exclude(pk=location.pk).update(is_home=False)  # never two homes
        return location_data(location), not existing


@transaction.atomic
def clear_home_location(club) -> bool:
    """Stop treating the location as home. The location itself stays (events may point at it). Returns whether there was one."""
    with schema_context(club.schema_name):
        return Location.objects.filter(is_home=True).update(is_home=False) > 0
