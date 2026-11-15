"""Club settings edited by a club admin (name, legal details, branding, logo). Admin only; every write is audited.

The tenant club is passed in by the API layer (``request.tenant``); ``Club`` lives in the public schema but the audit
trail is a tenant table, so the entry is written in the club's own schema (the current one while serving a request)."""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from PIL import Image

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
    data["event_background_url"] = club.event_background.url if club.event_background else None
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


def _image_extension(image, label: str) -> str:
    if image.size > MAX_PHOTO_BYTES:
        message = f"The {label} is larger than 5 MB."
        raise Invalid(message, {"file": [message]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"file": ["This is not a valid image."]}) from e
    if fmt not in PHOTO_FORMATS:
        message = f"Use a JPEG, PNG or WebP {label}."
        raise Invalid(message, {"file": [message]})
    image.seek(0)
    return PHOTO_FORMATS[fmt]


@transaction.atomic
def set_logo(actor, club: Club, image) -> Club:
    """Replace the club logo (JPEG, PNG or WebP up to 5 MB). ``Club.save`` regenerates the icons."""
    require_settings(actor)
    extension = _image_extension(image, "logo")
    old = club.logo.name if club.logo else ""
    club.logo.save(f"logo-{uuid.uuid4().hex[:12]}.{extension}", image, save=False)
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


def update_event_background(actor, club: Club, image=None) -> Club:
    """Replace or remove the club's event image without discarding it before commit."""
    require_settings(actor)
    extension = _image_extension(image, "event background") if image is not None else None
    storage = Club._meta.get_field("event_background").storage
    uploaded = None
    try:
        with transaction.atomic():
            club = Club.objects.select_for_update().get(pk=club.pk)
            old = club.event_background.name or ""
            if extension:
                club.event_background.save(f"background-{uuid.uuid4().hex[:12]}.{extension}", image, save=False)
                uploaded = club.event_background.name
            elif old:
                club.event_background = ""
            else:
                return club
            club.save(update_fields=["event_background", "modified"])
            audit.record(actor, "club.event_background_changed", target=club, changes={"event_background": {"from": old, "to": uploaded or ""}}, target_label=club.name)
            if old:
                transaction.on_commit(lambda: storage.delete(old), robust=True)
    except Exception:
        if uploaded:
            storage.delete(uploaded)
        raise
    return club
