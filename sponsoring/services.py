"""Tenant-scoped sponsor edits and their logo files."""

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.shortcuts import get_object_or_404
from PIL import Image

from members.services import access, audit
from members.services.errors import Invalid
from members.services.members import MAX_PHOTO_BYTES, PHOTO_FORMATS
from sponsoring.models import Sponsor


def _logo_info(logo):
    if logo.size > MAX_PHOTO_BYTES:
        raise Invalid("The logo is larger than 5 MB.", {"logo": ["Choose an image up to 5 MB."]})
    try:
        with Image.open(logo) as image:
            fmt, (width, height) = image.format, image.size
            image.verify()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as error:
        raise Invalid("This is not a valid image.", {"logo": ["This is not a valid image."]}) from error
    if fmt not in PHOTO_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP logo.", {"logo": ["Use a JPEG, PNG or WebP logo."]})
    logo.seek(0)
    return PHOTO_FORMATS[fmt], width, height


def save_sponsor(actor, values, *, sponsor_id=None, logo=None, remove_logo=False):
    """Validate details and any logo before making changes. Replace the old file after commit."""
    access.require_sponsors(actor)
    storage = Sponsor._meta.get_field("logo").storage
    written = ""
    try:
        with transaction.atomic():
            sponsor = get_object_or_404(Sponsor.objects.select_for_update(), pk=sponsor_id) if sponsor_id else Sponsor()
            old_logo = sponsor.logo.name or ""
            changes = audit.diff(sponsor, values) if sponsor_id else {}
            for key, value in values.items():
                setattr(sponsor, key, value)
            try:
                sponsor.full_clean(exclude=["logo"])
                if sponsor.url:
                    URLValidator(schemes=["http", "https"])(sponsor.url)
            except ValidationError as error:
                errors = getattr(error, "message_dict", {"url": error.messages})
                raise Invalid("Please correct the highlighted fields.", errors) from error

            if logo:
                extension, width, height = _logo_info(logo)
                sponsor.logo.save(f"logo-{uuid4().hex[:12]}.{extension}", logo, save=False)
                written = sponsor.logo.name
                sponsor.logo_width, sponsor.logo_height = width, height
            elif remove_logo:
                sponsor.logo = ""
            if logo or (remove_logo and old_logo):
                changes["logo"] = {"from": old_logo, "to": sponsor.logo.name or ""}
            if sponsor_id and not changes:
                return sponsor
            sponsor.save()
            action = "updated" if sponsor_id else "created"
            audit.record(actor, f"sponsor.{action}", target=sponsor, target_label=sponsor.name, changes=changes)
            if old_logo and old_logo != sponsor.logo.name:
                transaction.on_commit(lambda: storage.delete(old_logo), robust=True)
    except Exception:
        if written:
            storage.delete(written)
        raise
    return sponsor


@transaction.atomic
def delete_sponsor(actor, sponsor_id):
    access.require_sponsors(actor)
    sponsor = get_object_or_404(Sponsor.objects.select_for_update(), pk=sponsor_id)
    storage, old_logo = sponsor.logo.storage, sponsor.logo.name
    audit.record(actor, "sponsor.deleted", target=sponsor, target_label=sponsor.name)
    sponsor.delete()
    if old_logo:
        transaction.on_commit(lambda: storage.delete(old_logo), robust=True)
