"""Sponsors pay in fixed-date installments, invoiced in finance.

* An agreement (``SponsorAgreement``) lists the installments (date and amount), the contact the invoices go to and a grace period.
* Each installment is invoiced ``lead_days`` before its date as a shop *charge* (the invoice, its payments, reminders and PDF are finance's own)
  to a login-less member made from the contact (``shop.services.payers``). The daily job does it once; an invoice that is cancelled is made again.
* An installment's status follows the payments of its invoice (``payments.sync_payment_status`` calls ``on_order_changed``).
* A sponsor with an installment unpaid for more than ``grace_days`` past its due date is made inactive (``Sponsor.suspended_at``: no longer in
  ``Sponsor.active()``, so gone from the app and the public pages); it is active again once nothing is over the grace period any more."""

import datetime
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from members.services.errors import Invalid
from shop.models import Invoice, Order
from shop.services import charges, payers, payments
from sponsoring.models import Sponsor, SponsorAgreement, SponsorInstallment

log = logging.getLogger(__name__)

MAX_INSTALLMENTS = 24


def _alive(order) -> bool:
    return order is not None and order.fulfillment_status != Order.FulfillmentStatus.CANCELLED


def _invoice(order):
    return Invoice.objects.filter(order=order).first() if _alive(order) else None


def due_date_of(installment: SponsorInstallment, order=None) -> datetime.date:
    """The date the grace period counts from: the invoice's due date (an invoice made late is due later than the installment's date), else the installment's."""
    invoice = _invoice(order if order is not None else installment.order)
    return max(installment.due_date, invoice.due_date) if invoice is not None and invoice.due_date else installment.due_date


def status_of(installment: SponsorInstallment, today: datetime.date | None = None) -> dict:
    """``{"status": scheduled | open | partially_paid | paid | overdue, "overdue_days": int, "paid", "due"}`` for one installment. ``scheduled`` is
    an installment without a live invoice that is not yet due."""
    today = today or timezone.localdate()
    order = installment.order
    alive = _alive(order)
    paid = payments.amount_paid(order) if alive else Decimal("0")
    if alive and order.payment_status == Order.PaymentStatus.PAID:
        return {"status": "paid", "overdue_days": 0, "paid": paid, "due": Decimal("0")}
    due = max(Decimal("0"), (order.total if alive else installment.amount) - paid)
    deadline = due_date_of(installment)
    if today > deadline:
        return {"status": "overdue", "overdue_days": (today - deadline).days, "paid": paid, "due": due}
    if not alive:
        return {"status": "scheduled", "overdue_days": 0, "paid": paid, "due": due}
    return {"status": "partially_paid" if paid > 0 else "open", "overdue_days": 0, "paid": paid, "due": due}


def sponsor_status(sponsor: Sponsor, today: datetime.date | None = None) -> dict:
    """The sponsor's payment standing: ``none`` (no agreement), ``suspended`` (inactive for non-payment), ``overdue`` (with the days of the oldest),
    ``open`` (an invoice is out) or ``paid_up``. Also the next installment still to be paid."""
    today = today or timezone.localdate()
    agreement = getattr(sponsor, "agreement", None)
    if agreement is None:
        return {"status": "none", "overdue_days": 0, "next_due_date": None, "open_amount": Decimal("0")}
    rows = [(i, status_of(i, today)) for i in agreement.installments.select_related("order")]
    unpaid = [(i, s) for i, s in rows if s["status"] != "paid"]
    overdue_days = max((s["overdue_days"] for _i, s in unpaid), default=0)
    if sponsor.suspended_at:
        status = "suspended"
    elif overdue_days > 0:
        status = "overdue"
    elif any(s["status"] in ("open", "partially_paid") for _i, s in unpaid):
        status = "open"
    else:
        status = "paid_up"
    return {
        "status": status, "overdue_days": overdue_days, "next_due_date": min((i.due_date for i, _s in unpaid), default=None),
        "open_amount": sum((s["due"] for _i, s in unpaid if s["status"] != "scheduled"), Decimal("0")),
    }


# ------------------------------------------------------------------------------------------------- the agreement


def save_agreement(sponsor: Sponsor, values: dict, installments: list[dict]) -> SponsorAgreement:
    """Create or replace the agreement. ``installments`` are ``{"id"?, "due_date", "amount"}``. An installment that already has a live invoice can
    not change or go (cancel its invoice first); the others are replaced by the list."""
    name, email = (values.get("contact_name") or "").strip(), (values.get("contact_email") or "").strip()
    if not name:
        raise Invalid("Add a contact name.", {"contact_name": ["Add a contact name."]})
    if not email:
        raise Invalid("Add a contact email.", {"contact_email": ["Invoices are sent to this address."]})
    if not installments or len(installments) > MAX_INSTALLMENTS:
        raise Invalid("Add between 1 and 24 installments.", {"installments": [f"Add between 1 and {MAX_INSTALLMENTS} installments."]})
    for i, row in enumerate(installments):
        if row["amount"] is None or Decimal(row["amount"]) <= 0:
            raise Invalid("Invalid amount.", {f"installments.{i}": ["The amount must be more than zero."]})
    with transaction.atomic():
        agreement, created = SponsorAgreement.objects.select_for_update().get_or_create(sponsor=sponsor, defaults={"contact_name": name, "contact_email": email})
        if not created and (agreement.contact_name, agreement.contact_email.lower()) != (name, email.lower()):
            agreement.payer = None  # a new contact: the next invoice goes to them
        agreement.contact_name, agreement.contact_email = name, email
        agreement.description = (values.get("description") or "").strip()[:150]
        agreement.lead_days, agreement.grace_days = values.get("lead_days", 30), values.get("grace_days", 14)
        agreement.save()
        existing = {str(i.pk): i for i in agreement.installments.select_related("order")}
        keep = set()
        for i, row in enumerate(installments):
            current = existing.get(str(row.get("id") or ""))
            amount = Decimal(row["amount"]).quantize(Decimal("0.01"))
            if current is None:
                SponsorInstallment.objects.create(agreement=agreement, due_date=row["due_date"], amount=amount)
                continue
            keep.add(str(current.pk))
            if _alive(current.order):
                if (current.due_date, current.amount) != (row["due_date"], amount):
                    raise Invalid("An invoiced installment is fixed.", {f"installments.{i}": ["This installment is invoiced: cancel its invoice in finance to change it."]})
            else:
                current.due_date, current.amount = row["due_date"], amount
                current.save(update_fields=["due_date", "amount", "modified"])
        for key, current in existing.items():
            if key in keep:
                continue
            if _alive(current.order):
                raise Invalid("An invoiced installment stays.", {"installments": ["An installment with an invoice can not be removed: cancel the invoice in finance first."]})
            current.delete()
    evaluate(sponsor)
    return agreement


# ------------------------------------------------------------------------------------------------- invoicing


def payer_for(agreement: SponsorAgreement):
    if agreement.payer_id is None:
        agreement.payer = payers.external_payer(agreement.contact_name, agreement.contact_email, fallback_name=agreement.sponsor.name)
        agreement.save(update_fields=["payer", "modified"])
    return agreement.payer


def _label(installment: SponsorInstallment) -> str:
    agreement = installment.agreement
    rows = list(agreement.installments.all())
    number = next((n for n, r in enumerate(rows, 1) if r.pk == installment.pk), 1)
    base = agreement.description or _("Sponsorship")
    return _("%(base)s %(sponsor)s, installment %(n)s of %(total)s") % {"base": base, "sponsor": agreement.sponsor.name, "n": number, "total": len(rows)}


@transaction.atomic
def invoice_installment(installment: SponsorInstallment, actor=None, *, today: datetime.date | None = None) -> Order | None:
    """Make and send the installment's invoice unless it has a live one. Returns the new order, or None when there already was one."""
    today = today or timezone.localdate()
    installment = SponsorInstallment.objects.select_for_update(of=("self",)).select_related("agreement__sponsor", "order").get(pk=installment.pk)
    if _alive(installment.order):
        return None
    agreement = installment.agreement
    order = charges.create_draft(
        actor, member=payer_for(agreement), lines=[charges.ChargeLine(_label(installment), 1, installment.amount)], due_date=max(installment.due_date, today),
        notes=_("Sponsor installment"), invoice_text=agreement.description,
    )  # fmt: skip
    charges.send(actor, order)
    installment.order = order
    installment.save(update_fields=["order", "modified"])
    return order


def remind(installment: SponsorInstallment):
    """Send the open invoice of the installment again (counts as a reminder)."""
    invoice = Invoice.objects.filter(order=installment.order).first() if _alive(installment.order) else None
    if invoice is None:
        raise Invalid("There is no invoice to send again.", {"installment": ["Send the invoice first."]})
    return charges.send_reminder(invoice)


def invoice_due(today: datetime.date | None = None) -> int:
    """The daily job (in one club's schema): every installment whose invoice date has come and that has no live invoice gets one. Idempotent: an
    installment with an invoice is skipped. Returns how many were invoiced."""
    today = today or timezone.localdate()
    n = 0
    candidates = SponsorInstallment.objects.select_related("agreement__sponsor", "order").filter(Q(order__isnull=True) | Q(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED))
    for installment in candidates:
        agreement = installment.agreement
        sponsor = agreement.sponsor
        if today < installment.due_date - datetime.timedelta(days=agreement.lead_days) or (sponsor.end_date and sponsor.end_date < today):
            continue
        try:
            if invoice_installment(installment, today=today) is not None:
                n += 1
        except Exception:  # one sponsor's problem (no email ...) must not stop the others
            log.exception("sponsoring.invoice_failed", extra={"installment": str(installment.pk)})
    return n


# ------------------------------------------------------------------------------------------------- grace period


def evaluate(sponsor: Sponsor, today: datetime.date | None = None) -> bool | None:
    """Suspend the sponsor when an installment is unpaid past the grace period, reactivate it when none is. Returns True when it was suspended
    just now, False when reactivated, None for no change."""
    today = today or timezone.localdate()
    sponsor = Sponsor.objects.select_related("agreement").get(pk=sponsor.pk)
    agreement = getattr(sponsor, "agreement", None)
    if agreement is None:
        late = False
    else:
        late = any(s["status"] == "overdue" and s["overdue_days"] > agreement.grace_days for s in (status_of(i, today) for i in agreement.installments.select_related("order")))
    if late and sponsor.suspended_at is None:
        sponsor.suspended_at = timezone.now()
        sponsor.save(update_fields=["suspended_at", "modified"])
        return True
    if not late and sponsor.suspended_at is not None:
        sponsor.suspended_at = None
        sponsor.save(update_fields=["suspended_at", "modified"])
        return False
    return None


def enforce_grace(today: datetime.date | None = None) -> dict:
    """The daily job: ``evaluate`` every sponsor with an agreement (and any suspended one). Returns the numbers suspended and reactivated."""
    out = {"suspended": 0, "reactivated": 0}
    for sponsor in Sponsor.objects.filter(Q(agreement__isnull=False) | Q(suspended_at__isnull=False)):
        result = evaluate(sponsor, today)
        if result is True:
            out["suspended"] += 1
        elif result is False:
            out["reactivated"] += 1
    return out


def on_order_changed(order) -> None:
    """Called by finance after a payment or a cancellation: the sponsor an invoice belongs to is judged again (paying the arrears reactivates it at once)."""
    for installment in SponsorInstallment.objects.filter(order=order).select_related("agreement__sponsor"):
        evaluate(installment.agreement.sponsor)
