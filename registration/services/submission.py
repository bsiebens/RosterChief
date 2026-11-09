"""Submitting the public join page: validate, price and create everything in one transaction (ported from ``registration.services.submission``).

Per submission: a contact (always a **fresh** member -- an anonymous visitor is never matched to an existing person by email, staff merge them
later with ``linking``), the people being registered (the contact themselves and/or children), a family when children are involved (the contact
is its PARENT), and for each person a PENDING ``ClubMembership`` whose fee, early-bird deadline and discount come from ``pricing``. No login is
created. The batch stays out of the Sign-ups queue until staff confirm its invoice (``invoicing``).
"""

import datetime
import logging

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from club.services import create_member
from members.models import ClubMembership, Family, FamilyMembership
from members.services import audit
from members.services import memberships as fees
from members.services.errors import Invalid
from members.services.members import _clean
from registration.models import RegistrationBatch, RegistrationDetails
from registration.services import config, pricing
from teams.models import Position, Team

log = logging.getLogger(__name__)

MAX_ENTRIES = 12
LANGS = ("en", "nl", "fr")


class RegistrationClosed(Invalid):
    def __init__(self):
        super().__init__("Registration is closed.", {"__all__": ["Registration is closed."]}, code="closed")


def _text(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def validate(contact: dict, entries: list[dict]) -> list[dict]:
    """Check the form; returns the entries with resolved objects (``variant``, ``team``, ``position``) or raises ``Invalid`` with errors keyed
    ``contact.<field>`` / ``entries.<index>.<field>``."""
    errors: dict[str, list[str]] = {}
    if not _text(contact.get("first_name")):
        errors["contact.first_name"] = ["Required."]
    if not _text(contact.get("last_name")):
        errors["contact.last_name"] = ["Required."]
    email = _text(contact.get("email"))
    try:
        validate_email(email)
    except ValidationError:
        errors["contact.email"] = ["Enter a valid email address."]
    if not entries:
        errors["entries"] = ["Add at least one person."]
    if len(entries) > MAX_ENTRIES:
        errors["entries"] = [f"At most {MAX_ENTRIES} people at once."]
    variants = pricing.variants_by_id([e.get("variant_id") for e in entries if e.get("variant_id")])
    teams = {t.pk: t for t in Team.objects.filter(pk__in=[e["requested_team_id"] for e in entries if e.get("requested_team_id")])}
    positions = {p.pk: p for p in Position.objects.filter(pk__in=[e["requested_position_id"] for e in entries if e.get("requested_position_id")], staff_position=False)}
    today = timezone.localdate()
    out, selves = [], 0
    for i, entry in enumerate(entries):
        where = f"entries.{i}"
        who = entry.get("who") or "child"
        if who not in ("self", "child"):
            errors[f"{where}.who"] = ["Invalid choice."]
        selves += who == "self"
        kind = entry.get("kind") or RegistrationDetails.EntryKind.PLAYER
        if kind not in RegistrationDetails.EntryKind.values:
            errors[f"{where}.kind"] = ["Invalid choice."]
        first = _text(contact.get("first_name")) if who == "self" else _text(entry.get("first_name"))
        last = _text(contact.get("last_name")) if who == "self" else _text(entry.get("last_name"))
        if not first:
            errors[f"{where}.first_name"] = ["Required."]
        if not last:
            errors[f"{where}.last_name"] = ["Required."]
        dob = entry.get("date_of_birth")
        if who == "child" and dob is None:
            errors[f"{where}.date_of_birth"] = ["Required."]
        if dob is not None and dob > today:
            errors[f"{where}.date_of_birth"] = ["Can't be in the future."]
        variant = variants.get(entry.get("variant_id"))
        if variant is None:
            errors[f"{where}.variant_id"] = ["Choose what to register for."]
        elif kind in RegistrationDetails.EntryKind.values and not pricing.fits_kind(variant.product, kind):
            errors[f"{where}.variant_id"] = [pricing.kind_error(kind)]
        team = teams.get(entry.get("requested_team_id")) if entry.get("requested_team_id") else None
        if entry.get("requested_team_id") and team is None:
            errors[f"{where}.requested_team_id"] = ["Unknown team."]
        position = positions.get(entry.get("requested_position_id")) if entry.get("requested_position_id") else None
        if entry.get("requested_position_id") and position is None:
            errors[f"{where}.requested_position_id"] = ["Unknown position."]
        jersey = entry.get("requested_jersey_number")
        if jersey is not None and not 0 <= jersey <= 99:
            errors[f"{where}.requested_jersey_number"] = ["Between 0 and 99."]
        out.append({**entry, "who": who, "kind": kind, "first_name": first, "last_name": last, "variant": variant, "team": team, "position": position})
    if selves > 1:
        errors["entries"] = ["Only one person can be the contact."]
    if errors:
        raise Invalid("Check the registration.", errors)
    return out


def _new_member(club, features, first, last, email="", *, phone="", dob=None, consent=False, language=""):
    member = create_member(club, first, last, email, features=features)  # enforces the club's member limit
    member.phone, member.date_of_birth, member.photo_public_consent = phone or "", dob, bool(consent)
    if language in LANGS:
        member.language = language
    _clean(member)
    member.save()
    return member


@transaction.atomic
def submit_registration(club, *, contact: dict, entries: list[dict], language: str = "", features=None, today: datetime.date | None = None) -> RegistrationBatch:
    """Create the batch, its people, family and pending memberships. Raises ``Invalid`` (422) / ``RegistrationClosed`` / ``LimitExceeded``."""
    if not config.get_settings().is_open:
        raise RegistrationClosed()
    today = today or timezone.localdate()
    checked = validate(contact, entries)
    priced = pricing.price_entries([e["variant"] for e in checked], today=today)
    language = language if language in LANGS else ""

    contact_member = _new_member(club, features, _text(contact["first_name"]), _text(contact["last_name"]), _text(contact["email"]), phone=_text(contact.get("phone")), language=language)
    batch = RegistrationBatch.objects.create(contact=contact_member, season=priced.season, language=language)
    family = None
    if any(e["who"] == "child" for e in checked):
        family = Family.objects.create(name=contact_member.last_name)
        FamilyMembership.objects.create(family=family, member=contact_member, role=FamilyMembership.FamilyRole.PARENT)
        audit.record(None, "family.created", target=family, changes=audit.snapshot(family, ["name"]))
    for entry, line in zip(checked, priced.lines, strict=True):
        if entry["who"] == "self":
            member = contact_member
            member.date_of_birth, member.photo_public_consent = entry.get("date_of_birth"), bool(entry.get("photo_public_consent"))
            member.save(update_fields=["date_of_birth", "photo_public_consent", "modified"])
        else:
            member = _new_member(club, features, entry["first_name"], entry["last_name"], _text(entry.get("email")), dob=entry.get("date_of_birth"), consent=entry.get("photo_public_consent"), language=language)
            FamilyMembership.objects.create(family=family, member=member, role=FamilyMembership.FamilyRole.CHILD)
        membership = ClubMembership.objects.create(
            member=member, season=priced.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=line.price, signed_up_at=today,
            early_payment_deadline=line.early_bird_deadline if line.early_bird_discount else None, early_payment_discount=line.early_bird_discount,
        )  # fmt: skip
        fees.sync_fee_status(membership)
        audit.record(None, "membership.created", member=member, target=membership, changes=audit.snapshot(membership, fees.LOGGED_FIELDS), target_label=f"{priced.season} - {member}")
        RegistrationDetails.objects.create(
            batch=batch, membership=membership, member=member, entry_kind=entry["kind"], requested_team=entry["team"], requested_position=entry["position"],
            requested_jersey_number=entry.get("requested_jersey_number"), product_variant=line.variant, description=line.variant.full_name, base_price=line.base, discount_amount=line.discount,
        )  # fmt: skip
    from registration.services import notifications

    transaction.on_commit(lambda: notifications.send_confirmation(batch.pk))
    return batch
