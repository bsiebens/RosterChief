"""The billing review of a registration (ported from the old ``registration.services.invoicing``).

Until staff confirm a batch its memberships are hidden from the Sign-ups queue (``awaiting_membership_ids``). In the review staff can edit a line's
price, leave a line out and give a manual discount with a note; ``recompute`` writes the result back onto each membership's ``fee_amount``
(idempotent: always derived from the lines, never from earlier deltas). Confirming numbers the invoice, sets its due date, places the chosen teams
and emails the contact (the status update, with the invoice PDF attached unless ``send`` is false). Everything here needs ``members.manage_members``.
"""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from members.models import ClubMembership, Member
from members.services import audit
from members.services import memberships as fees
from members.services.access import require_finance
from members.services.errors import Invalid
from registration.models import RegistrationBatch, RegistrationDetails
from registration.services import config, pricing
from teams.models import Team, TeamMembership

CENT = Decimal("0.01")


def awaiting_membership_ids() -> set:
    """Ids of the memberships that belong to a registration staff have not confirmed (or cancelled) yet."""
    return set(RegistrationDetails.objects.filter(batch__confirmed_at__isnull=True, batch__cancelled_at__isnull=True).values_list("membership_id", flat=True))


def awaiting_only_member_ids():
    """Ids of the people whose every membership is of an unconfirmed registration: until the review is done they are not members yet
    (hidden from the members list the way their memberships are hidden from the queue and the overview)."""
    from django.db.models import Exists, OuterRef

    awaiting = awaiting_membership_ids()
    confirmed_elsewhere = ClubMembership.objects.exclude(pk__in=awaiting).filter(member=OuterRef("pk"))
    return Member.objects.filter(pk__in=ClubMembership.objects.filter(pk__in=awaiting).values("member")).exclude(Exists(confirmed_elsewhere)).values("pk")


def _editable(batch: RegistrationBatch) -> None:
    if batch.legacy_invoice_history and not batch.details.exists():
        raise Invalid("This imported invoice is available as read-only history.", code="legacy_invoice")
    if batch.cancelled_at:
        raise Invalid("This registration was cancelled.", {"__all__": ["This registration was cancelled."]}, code="cancelled")
    if batch.confirmed_at:
        raise Invalid("The invoice is already confirmed.", {"__all__": ["The invoice is already confirmed."]}, code="confirmed")


def subtotal(batch: RegistrationBatch) -> Decimal:
    """What the registration costs before the manual discount: the lines less the credits (credit lines count nothing themselves)."""
    return sum((d.payable for d in batch.details.all()), Decimal("0.00"))


def total(batch: RegistrationBatch) -> Decimal:
    return max(subtotal(batch) - batch.manual_discount_amount, Decimal("0.00"))


def credits(batch: RegistrationBatch) -> tuple[Decimal, Decimal]:
    """``(credit_total, credit_applied)``: the negative lines of the registration (as a positive amount) and how much of that came off the other lines."""
    details = list(batch.details.all())
    return sum((-d.price for d in details if d.is_credit), Decimal("0.00")), sum((d.credit_share for d in details), Decimal("0.00"))


def order_discounts(batch: RegistrationBatch) -> list[dict]:
    """The per-registration (multi-registrant) discounts of the registration, one per product that has one: ``{"product_id", "name", "amount",
    "people"}`` with ``amount`` positive. Lines staff left out or gave their own price count nothing (see ``RegistrationDetails.order_share``)."""
    found: dict = {}
    for d in batch.details.select_related("product"):
        share = d.order_share
        if share <= 0:
            continue
        key = d.product_id or d.description
        row = found.setdefault(key, {"product_id": d.product_id, "name": d.product.name if d.product_id else d.description, "amount": Decimal("0.00"), "people": 0})
        row["amount"] += share
        row["people"] += 1
    people = {}
    for d in batch.details.select_related("product"):
        if d.product_id and not d.excluded and not d.is_credit:
            people[d.product_id] = people.get(d.product_id, 0) + 1
    for row in found.values():
        product = next((d.product for d in batch.details.select_related("product") if d.product_id == row["product_id"]), None) if row["product_id"] else None
        row["percent"] = pricing.tier_percent(product, people.get(row["product_id"], row["people"])) if product is not None else None
    return list(found.values())


def _spread_credits(details) -> None:
    """Recompute every line's credit share from the current prices (after staff edited or excluded lines) and save the changed ones."""
    rows = []
    for d in details:
        price = d.before_credit
        rows.append(SimpleNamespace(is_credit=d.is_credit and not d.excluded, base=d.price if d.is_credit else price, before_credit=price, credit_share=Decimal("0.00")))
    pricing.spread_credits(rows)
    for d, row in zip(details, rows, strict=True):
        if d.credit_share != row.credit_share:
            d.credit_share = row.credit_share
            d.save(update_fields=["credit_share", "modified"])


def recompute(actor, batch: RegistrationBatch) -> None:
    """Write the line prices less the credits and the manual discount (spread in proportion to the lines) onto every membership's fee."""
    details = list(batch.details.select_related("membership"))
    _spread_credits(details)
    base = sum((d.payable for d in details), Decimal("0.00"))
    discount = min(batch.manual_discount_amount, base)
    shares, given = {}, Decimal("0.00")
    for d in details:
        share = (discount * d.payable / base).quantize(CENT) if base else Decimal("0.00")
        shares[d.pk] = share
        given += share
    if details and discount:
        first = next(d for d in details if d.payable > 0)
        shares[first.pk] += discount - given  # the cents that rounding left over
    for d in details:
        m = d.membership
        fee = max(d.payable - shares[d.pk], Decimal("0.00"))
        changes = audit.diff(m, {"fee_amount": fee, "early_payment_discount": min(m.early_payment_discount, fee)})
        if changes:
            m.fee_amount, m.early_payment_discount = fee, min(m.early_payment_discount, fee)
            m.save(update_fields=["fee_amount", "early_payment_discount", "modified"])
            changes.update(fees.sync_fee_status(m))
            audit.record(actor, "membership.updated", member=m.member, target=m, changes=changes, target_label=f"{m.season} - {m.member}")


@transaction.atomic
def reprice(actor, batch: RegistrationBatch, *, today: datetime.date | None = None) -> bool:
    """Price a registration that is still waiting for review again with the CURRENT pricing rules (tiers counted per product, per-registration
    discounts and credits as their own shares) and write the result onto its lines and memberships. Staff overrides and left-out lines stay as
    they are; the manual discount is kept. Confirmed or cancelled registrations are never touched (they were invoiced). Returns whether anything changed."""
    if batch.confirmed_at or batch.cancelled_at:
        return False
    details = list(batch.details.select_related("membership", "product", "product_variant"))
    options = [pricing.Option(product=d.product, variant=d.product_variant) for d in details if d.product_id]
    if len(options) != len(details) or not details:
        return False  # a line without a product cannot be priced again
    priced = pricing.price_entries(options, today=today or timezone.localtime(batch.created).date())
    changed = False
    for d, line in zip(details, priced.lines, strict=True):
        values = {"base_price": line.base, "discount_amount": line.discount, "order_discount_share": line.order_discount, "credit_share": line.credit_share}
        if any(getattr(d, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(d, k, v)
            d.save(update_fields=[*values, "modified"])
            changed = True
        m = d.membership
        early = (line.early_bird_deadline if line.early_bird_discount else None, line.early_bird_discount)
        if (m.early_payment_deadline, m.early_payment_discount) != early:
            m.early_payment_deadline, m.early_payment_discount = early
            m.save(update_fields=["early_payment_deadline", "early_payment_discount", "modified"])
            changed = True
    recompute(actor, batch)
    return changed


@transaction.atomic
def update_review(actor, batch: RegistrationBatch, *, lines=None, manual_discount_amount=None, manual_discount_note=None) -> RegistrationBatch:
    """Edit the review: ``lines`` = ``[{id, price (a number, or null to go back to the computed price), excluded}]`` (only the keys present change)."""
    require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    _editable(batch)
    by_id = {d.pk: d for d in batch.details.all()}
    errors = {}
    for i, row in enumerate(lines or []):
        d = by_id.get(row["id"])
        if d is None:
            errors[f"lines.{i}.id"] = ["Not a line of this registration."]
            continue
        if "price" in row.keys():
            price = row["price"]
            if price is not None and price < 0:
                errors[f"lines.{i}.price"] = ["Can't be negative."]
                continue
            if d.is_credit and price is not None:
                errors[f"lines.{i}.price"] = ["A credit line has no price to override. Leave it out instead."]
                continue
            d.price_override = fees.money(price) if price is not None else None
        if row.get("excluded") is not None:
            d.excluded = bool(row["excluded"])
        if "requested_team_id" in row.keys():
            team_id = row["requested_team_id"]
            team = Team.objects.filter(pk=team_id).first() if team_id is not None else None
            if team_id is not None and team is None:
                errors[f"lines.{i}.requested_team_id"] = ["Unknown team."]
                continue
            if team is not None and d.entry_kind != d.EntryKind.PLAYER:
                errors[f"lines.{i}.requested_team_id"] = ["Only players are placed in a team."]
                continue
            d.requested_team = team
        d.save(update_fields=["price_override", "excluded", "requested_team", "modified"])
    if manual_discount_amount is not None:
        try:
            amount = fees.money(manual_discount_amount)
        except Invalid:
            errors["manual_discount_amount"] = ["Invalid amount."]
        else:
            if amount > subtotal(batch):
                errors["manual_discount_amount"] = ["More than the registration costs."]
            batch.manual_discount_amount = amount
    if manual_discount_note is not None:
        batch.manual_discount_note = manual_discount_note.strip()[:255]
    if errors:
        raise Invalid("Check the review.", errors)
    batch.save(update_fields=["manual_discount_amount", "manual_discount_note", "modified"])
    recompute(actor, batch)
    return batch


def _next_number(today: datetime.date) -> str:
    """``REG-<year>-0001`` ...: per club, unique, and race-free because the settings row is locked while numbering."""
    settings_row = config.get_settings()
    type(settings_row).objects.select_for_update().get(pk=settings_row.pk)
    prefix = f"REG-{today.year}-"
    taken = [int(n[len(prefix) :]) for n in RegistrationBatch.objects.filter(invoice_number__startswith=prefix).values_list("invoice_number", flat=True) if n[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1:04d}"


def _place_requested_teams(actor, batch: RegistrationBatch) -> list:
    """Best effort: roster everybody on the team they asked for. A jersey number that is taken or out of range is dropped, not an error: the ids of
    the lines whose number was dropped are returned so the review can say so."""
    from teams.services import teams as team_services

    dropped = []
    for d in batch.details.select_related("member", "requested_team", "requested_position"):
        team = d.requested_team
        if team is None or d.excluded or TeamMembership.objects.filter(team=team, season=batch.season, member=d.member).exists():
            continue
        number = d.requested_jersey_number
        try:
            team_services._check_number(team, batch.season, d.member, number)
        except Invalid:
            if number is not None:
                dropped.append(d.pk)
            number = None
        spot = TeamMembership.objects.create(team=team, member=d.member, season=batch.season, position=d.requested_position, jersey_number=number)
        d.resulting_team_membership = spot
        d.save(update_fields=["resulting_team_membership", "modified"])
        audit.record(actor, "team.roster_added", member=d.member, target=spot, target_label=f"{d.member} - {team.name}")
    return dropped


def number_state(d: RegistrationDetails) -> str | None:
    """What the requested jersey number of a line stands for, for the staff review: ``reserved`` (held for this registration while it waits),
    ``unavailable`` (it cannot be given any more: taken, reserved or out of range), ``assigned`` / ``dropped`` once the registration is confirmed,
    ``None`` when no number is involved (no number, volunteer, excluded line, cancelled registration)."""
    from registration.services import holds
    from teams.services import numbers

    number, batch = d.requested_jersey_number, d.batch
    if number is None or d.entry_kind != d.EntryKind.PLAYER or d.excluded or batch.cancelled_at:
        return None
    if batch.confirmed_at:
        spot = d.resulting_team_membership
        if spot is None:
            return None
        return "assigned" if spot.jersey_number == number else "dropped"
    team = d.requested_team
    if team is None:
        return None
    others = [h for h in holds.holders(batch.season, team) if h.member_id != d.member_id]
    return "reserved" if numbers.number_available(number, batch.season, team, d.member.date_of_birth, others) else "unavailable"


@transaction.atomic
def set_installments(actor, batch: RegistrationBatch, offsets: list[int]) -> RegistrationBatch:
    """Staff set (or clear, with ``[]``) the fixed instalment plan of a confirmed invoice. Each person's fee is split in equal parts over the first due date
    and ``offsets`` days after it; payments already made settle the oldest open part first, so changing the plan later never loses a payment."""
    require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.cancelled_at:
        raise Invalid("This registration is cancelled.", code="cancelled")
    if not batch.confirmed_at or not batch.invoice_due_date:
        raise Invalid("Confirm the invoice first.", code="not_confirmed")
    offsets = list(offsets)
    if not config.valid_offsets(offsets):
        raise Invalid("Check the instalments.", {"offsets": ["Enter up to 11 later payments, each later than the one before, within 365 days."]})
    if offsets and (datetime.date.max - batch.invoice_due_date).days < offsets[-1]:
        raise Invalid("The instalment schedule would end beyond the last supported date.", {"offsets": ["Choose fewer or shorter instalments."]})
    if offsets != batch.installment_offsets:
        before = batch.installment_offsets
        batch.installment_offsets = offsets
        batch.save(update_fields=["installment_offsets", "modified"])
        for d in batch.details.select_related("member"):
            audit.record(actor, "registration.installments_set", member=d.member, target=batch, changes={"installment_offsets": {"from": before, "to": offsets}}, target_label=batch.invoice_number or "")
    return batch


@transaction.atomic
def confirm_invoice(actor, batch: RegistrationBatch, *, due_date: datetime.date | None = None, send: bool = True) -> RegistrationBatch:
    """Number the invoice, set its due date, roster the requested teams and email the contact that the registration is confirmed (with the invoice PDF
    attached unless ``send`` is false). After this the memberships show up in the Sign-ups queue and the review can no longer be edited."""
    require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    _editable(batch)
    today = timezone.localdate()
    batch.invoice_due_date = due_date or today + datetime.timedelta(days=config.get_settings().invoice_due_days)
    if batch.installment_offsets and (datetime.date.max - batch.invoice_due_date).days < batch.installment_offsets[-1]:
        raise Invalid("The instalment schedule would end beyond the last supported date.", {"due_date": ["Choose an earlier date."]})
    recompute(actor, batch)
    batch.invoice_number = _next_number(today)
    batch.confirmed_at = timezone.now()
    batch.save(update_fields=["invoice_number", "invoice_due_date", "confirmed_at", "modified"])
    batch.numbers_dropped = _place_requested_teams(actor, batch)  # read by the API response
    for d in batch.details.select_related("membership"):
        fees.sync_fee_status(d.membership)  # a fee of 0 (a volunteer, a credit line) is settled from now on
    for d in batch.details.select_related("member", "membership"):
        audit.record(actor, "registration.confirmed", member=d.member, target=batch, changes={"invoice_number": {"from": None, "to": batch.invoice_number}}, target_label=batch.invoice_number)
    from registration.services import notifications

    # The registrant always hears that the registration is confirmed (one mail); ``send`` only decides whether the invoice PDF is attached to it.
    transaction.on_commit(lambda: notifications.send_confirmed(batch.pk, with_invoice=send))
    return batch


def _removable(membership: ClubMembership) -> bool:
    """A person who is nothing but this unpaid registration (no other season, no login, no payment) is deleted on cancel."""
    member = membership.member
    return fees.can_delete_membership(membership) and not member.user_id and not ClubMembership.objects.filter(member=member).exclude(pk=membership.pk).exists()


@transaction.atomic
def cancel_batch(actor, batch: RegistrationBatch) -> RegistrationBatch:
    """Turn the whole registration down. Each person who is nothing but this registration is removed together with the roster spot and family link
    it created; anyone else keeps their record and only the membership is cancelled (payments stay). The contact goes too when nothing else
    keeps them (no membership, login or other family member)."""
    require_finance(actor)
    batch = RegistrationBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.legacy_invoice_history and not batch.details.exists():
        raise Invalid("This imported invoice is available as read-only history.", code="legacy_invoice")
    if batch.cancelled_at:
        raise Invalid("This registration was cancelled.", {"__all__": ["Already cancelled."]}, code="cancelled")
    from members.models import Family
    from members.services import members as member_services

    contact = batch.contact
    for d in list(batch.details.select_related("membership", "member", "resulting_team_membership")):
        m = d.membership
        if m.status != ClubMembership.StatusChoices.PENDING:
            continue
        if _removable(m):
            member_services.delete_member(actor, d.member, authorized=True)
        else:
            if d.resulting_team_membership_id:
                d.resulting_team_membership.delete()
            fees.cancel(actor, m, authorized=True)
    if contact is not None and Member.objects.filter(pk=contact.pk).exists() and not contact.user_id and not ClubMembership.objects.filter(member=contact).exists():
        families = list(Family.objects.filter(memberships__member=contact))
        if all(not f.memberships.exclude(member=contact).exists() for f in families):
            for f in families:
                f.delete()
            member_services.delete_member(actor, contact, authorized=True)
            batch.contact = None
    batch.cancelled_at = timezone.now()
    batch.save(update_fields=["cancelled_at", "modified"])
    return batch
