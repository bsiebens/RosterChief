"""Turns a registration form's submission into real records -- a Member (+
family link) and a PENDING ClubMembership per entry, landing straight in
the existing Sign-up queue (club.services.onboarding) rather than a second,
parallel review gate. See registration/models.py's own module docstrings
for why RegistrationBatch/RegistrationDetails carry no status of their own.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from club.models import ClubMembership
from members.models import Family, FamilyMembership, Member
from members.services.family import add_parent_to_family, find_member_by_email, get_or_create_login_member
from shop.models import ProductVariant
from teams.models import Position, Team

from ..models import RegistrationBatch, RegistrationDetails
from .pricing import PricingError, price_entries, resolve_registration_season


class RegistrationError(Exception):
    """A registration couldn't be submitted as given."""


@dataclass
class EntryInput:
    """One row of the registration form -- either a brand-new person
    (first_name/last_name/date_of_birth) or an already-known one
    (existing_member, e.g. the mobile re-registration screen picking one of
    the signed-in member's own managed_people). ``is_contact`` marks the one
    entry (if any) that *is* the person filling in the form themselves --
    told explicitly by the caller rather than inferred by name-matching,
    since that's the only unambiguous way to know it."""

    first_name: str = ""
    last_name: str = ""
    date_of_birth: date | None = None
    entry_kind: str = RegistrationDetails.EntryKind.PLAYER
    requested_team: Team | None = None
    requested_position: Position | None = None
    product_variant: ProductVariant | None = None
    existing_member: Member | None = None
    is_contact: bool = False


def _resolve_contact_member(submitted_by_user, contact_email):
    """The Member behind whoever is filling in the form, if already known --
    an authenticated submitter's own account (mobile re-registration) takes
    priority over an email lookup (the public flow), same precedence
    members.services.claims.approve_claim uses for its own submitter."""
    if submitted_by_user is not None and submitted_by_user.is_authenticated:
        member = Member.objects.filter(user=submitted_by_user).first()
        if member is not None:
            return member
    return find_member_by_email(contact_email)


@transaction.atomic
def submit_registration(club, *, contact_first_name, contact_last_name, contact_email, contact_phone="", entries, submitted_by_user=None):
    """``entries`` -- a list of ``EntryInput``. Returns the created
    ``RegistrationBatch``. Raises ``RegistrationError`` if the batch can't
    be priced/scoped as submitted (empty, or chosen products spanning more
    than one season)."""
    if not entries:
        raise RegistrationError("At least one person must be registered.")

    variants = [entry.product_variant for entry in entries]
    try:
        season = resolve_registration_season(variants)
    except PricingError as error:
        raise RegistrationError(str(error)) from error

    priced = price_entries(variants)

    contact_first_name = contact_first_name.strip()
    contact_last_name = contact_last_name.strip()
    contact_email = contact_email.strip().lower()

    parent = _resolve_contact_member(submitted_by_user, contact_email)
    if parent is None:
        parent = get_or_create_login_member(contact_email, contact_first_name, contact_last_name)

    parent_family_membership = parent.family_memberships.first()
    family = parent_family_membership.family if parent_family_membership is not None else Family.objects.create()
    if parent_family_membership is None:
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

    has_self_entry = any(entry.is_contact for entry in entries)
    if not has_self_entry:
        # A pure guardian -- no fee, active immediately, same as
        # members.services.claims.approve_claim's own guardian enrolment.
        add_parent_to_family(club, season, family, parent=parent)

    batch = RegistrationBatch.objects.create(
        club=club,
        season=season,
        submitted_by_user=submitted_by_user if submitted_by_user is not None and submitted_by_user.is_authenticated else None,
        contact_first_name=contact_first_name,
        contact_last_name=contact_last_name,
        contact_email=contact_email,
        contact_phone=contact_phone.strip(),
    )

    subtotal = Decimal("0")
    discount_total = Decimal("0")

    for entry, row in zip(entries, priced, strict=True):
        member = _resolve_entry_member(family, parent, entry)

        price = row["price"] - row["min_registrants_discount"]
        membership, _created = ClubMembership.objects.get_or_create(
            club=club,
            member=member,
            season=season,
            defaults={
                "kind": ClubMembership.Kind.MEMBER,
                "status": ClubMembership.StatusChoices.PENDING,
                "fee_amount": price,
                "fee_status": ClubMembership.FeeStatus.UNPAID,
                "signed_up_at": timezone.localdate(),
                "early_payment_deadline": row["deadline"],
                "early_payment_discount": row["deadline_discount"],
            },
        )
        RegistrationDetails.objects.update_or_create(
            membership=membership,
            defaults={
                "batch": batch,
                "entry_kind": entry.entry_kind,
                "requested_team": entry.requested_team,
                "requested_position": entry.requested_position if entry.entry_kind == RegistrationDetails.EntryKind.VOLUNTEER else None,
                "product_variant": row["variant"],
                "price": row["price"],
                "discount_amount": row["min_registrants_discount"],
            },
        )
        subtotal += row["price"]
        discount_total += row["min_registrants_discount"]

    batch.subtotal = subtotal
    batch.discount_amount = discount_total
    batch.total = subtotal - discount_total
    batch.save(update_fields=["subtotal", "discount_amount", "total"])

    return batch


def _resolve_entry_member(family, parent, entry):
    if entry.is_contact:
        return parent
    if entry.existing_member is not None:
        FamilyMembership.objects.get_or_create(family=family, member=entry.existing_member, defaults={"role": FamilyMembership.FamilyRole.CHILD})
        return entry.existing_member

    child = Member.objects.create(first_name=entry.first_name.strip(), last_name=entry.last_name.strip(), date_of_birth=entry.date_of_birth)
    FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
    return child
