"""Club settings edited by a club admin (name, legal details, branding, logo). Admin only; every write is audited.

The tenant club is passed in by the API layer (``request.tenant``); ``Club`` lives in the public schema but the audit
trail is a tenant table, so the entry is written in the club's own schema (the current one while serving a request)."""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from club.models import Club
from members.services import audit
from members.services.access import require_settings
from members.services.errors import Invalid
from members.services.members import MAX_PHOTO_BYTES, PHOTO_FORMATS

TEXT_FIELDS = ("name", "legal_name", "contact_email", "website", "legal_address", "legal_zip_code", "legal_city", "legal_country", "vat_id", "payment_instructions")
COLOR_FIELDS = ("primary_color", "secondary_color", "primary_text_color", "secondary_text_color")
FIELDS = TEXT_FIELDS + COLOR_FIELDS


def settings_out(club: Club) -> dict:
    data = {name: str(getattr(club, name) or "") for name in FIELDS}
    data["logo_url"] = club.logo.url if club.logo else None
    return data


def _messages(error: ValidationError) -> list[str]:
    return [str(m) for m in error.messages]


@transaction.atomic
def update_settings(actor, club: Club, values: dict) -> Club:
    """Apply the given fields (partial). 422 with per-field messages when a value is invalid; an empty string clears
    an optional field (a colour included). Nothing is saved, or audited, when nothing changed."""
    require_settings(actor)
    errors: dict[str, list[str]] = {}
    new = {}
    for name, value in values.items():
        if name not in FIELDS:
            continue
        value = (value or "").strip() if isinstance(value, str) or value is None else value
        field = Club._meta.get_field(name)
        try:
            new[name] = field.clean(value, club)
        except ValidationError as e:
            errors[name] = _messages(e)
    candidate = Club(**{f: getattr(club, f) for f in COLOR_FIELDS})
    for name in COLOR_FIELDS:
        if name in new:
            setattr(candidate, name, new[name])
    try:
        candidate.clean()  # e.g. white is refused as a brand colour
    except ValidationError as e:
        errors.update({k: [str(m) for m in v] for k, v in e.message_dict.items() if k in new})
    if errors:
        raise Invalid("Please correct the highlighted fields.", errors)
    changes = audit.diff(club, {k: str(v) if v is not None else "" for k, v in new.items()}) if new else {}
    # diff() compares to the stored value; CountryField values are Country objects, compare as strings
    changes = {k: c for k, c in changes.items() if str(c["from"] or "") != str(c["to"] or "")}
    if not changes:
        return club
    for name in changes:
        setattr(club, name, new[name])
    club.save(update_fields=[*changes, "modified"])
    audit.record(actor, "club.settings_updated", target=club, changes=changes, target_label=club.name)
    return club


@transaction.atomic
def set_logo(actor, club: Club, image) -> Club:
    """Replace the club logo (JPEG, PNG or WebP up to 5 MB, verified by decoding). ``Club.save`` regenerates the icons."""
    from PIL import Image, UnidentifiedImageError

    require_settings(actor)
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
    old = club.logo.name if club.logo else ""
    club.logo.save(f"logo-{uuid.uuid4().hex[:12]}.{PHOTO_FORMATS[fmt]}", image, save=False)
    club.save(update_fields=["logo", "modified"])
    if old:
        club.logo.storage.delete(old)
    audit.record(actor, "club.logo_changed", target=club, changes={"logo": {"from": bool(old), "to": True}}, target_label=club.name)
    return club


@transaction.atomic
def remove_logo(actor, club: Club) -> Club:
    require_settings(actor)
    if not club.logo:
        return club
    old = club.logo.name
    storage = club.logo.storage
    club.logo = ""
    club.save(update_fields=["logo", "modified"])
    storage.delete(old)
    audit.record(actor, "club.logo_changed", target=club, changes={"logo": {"from": True, "to": False}}, target_label=club.name)
    return club
