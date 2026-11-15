"""Staff side of public registration. ``/registrations``: the billing review (unconfirmed registrations, edit lines and a manual discount,
confirm the invoice, cancel, resend emails); ``/registration/settings``. Everything needs ``registration.manage_finance`` (403 otherwise; ``members.manage_members``
does not open it), except the registration settings (``members.manage_members``). The same router is the finance page: balances, payments against the
invoice (all or per person, or with a shop voucher) and the invoice PDF. Rules live in ``registration.services``."""

import uuid
from decimal import Decimal

from django.db.models import Count, F, Prefetch, Q
from django.http import HttpResponse
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError

from api.schemas_registrations import (
    ConfirmIn,
    InvoicePaymentIn,
    MarkInvoicePaidIn,
    RegistrationDetail,
    RegistrationListOut,
    RegistrationSettingsIn,
    RegistrationSettingsOut,
    ReviewIn,
    SentOut,
    VoucherLookupOut,
)
from members.models import ClubMembership, FeePayment
from members.services import access
from members.services import members as member_services
from registration.models import RegistrationBatch, RegistrationDetails
from registration.services import config, installments, invoicing, notifications
from registration.services import payments as pay
from shop.services import orders as shop_orders
from shop.services import vouchers
from shop.services.invoices import PDFUnavailable

router = Router()
settings_router = Router()


def _gate(request):
    access.require_finance(request.user)


def _settings_gate(request):
    access.require_admin(request.user)


def _batch(batch_id) -> RegistrationBatch:
    details = RegistrationDetails.objects.select_related(
        "member", "membership", "product", "requested_team", "requested_team__pool", "requested_position", "resulting_team_membership"
    ).prefetch_related("membership__payments")
    batch = RegistrationBatch.objects.select_related("contact", "season").prefetch_related(Prefetch("details", queryset=details)).filter(pk=batch_id).first()
    if batch is None:
        raise HttpError(404, "Not found.")
    return batch


def _person(m) -> dict | None:
    return {"id": m.pk, "name": str(m), "email": m.email} if m is not None else None


def _item(b: RegistrationBatch, schedule=None) -> dict:
    totals = pay.summary(b) if b.confirmed_at else {"due": 0, "paid": 0, "balance": 0}
    history = b.legacy_invoice_history
    history_only = bool(history and not b.details.all())
    upcoming = installments.next_unpaid(installments.batch_schedule(b)[0] if schedule is None else schedule)
    return {
        "id": b.pk, "status": b.status, "contact": _person(b.contact), "season_name": b.season.name, "submitted_at": b.submitted_at, "people_count": len({line["membership_id"] for line in history["lines"]}) if history_only else len(b.details.all()),
        "total": history["total"] if history_only else invoicing.total(b), "invoice_number": b.invoice_number, "confirmed_at": b.confirmed_at, "invoice_due_date": b.invoice_due_date,
        "amount_due": totals["due"], "amount_paid": totals["paid"], "balance": totals["balance"], "payment_status": None if history_only else pay.status(b, totals, next_installment=upcoming),
        "legacy_invoice_only": history_only, "installment_offsets": b.installment_offsets, "next_installment": upcoming,
    }  # fmt: skip


def _out(request, b: RegistrationBatch, numbers_dropped=()) -> dict:
    return _detail(b, numbers_dropped, vouchers_enabled=request.features.has("shop"))


def _payment_rows(b: RegistrationBatch) -> list[dict]:
    rows = FeePayment.objects.filter(membership__registration_details__batch=b).select_related("membership__member", "recorded_by", "voucher").order_by("-paid_at", "-created")
    rows = list(rows)
    names = member_services.user_names(p.recorded_by for p in rows)
    return [
        {"id": p.pk, "member": _person(p.membership.member), "membership_id": p.membership_id, "amount": p.amount, "method": p.method, "reference": p.reference, "paid_at": p.paid_at, "note": p.note,
         "recorded_by": names.get(p.recorded_by_id), "voucher_number": p.voucher.number if p.voucher else None}
        for p in rows
    ]  # fmt: skip


def _detail(b: RegistrationBatch, numbers_dropped=(), *, vouchers_enabled=False) -> dict:
    schedule, people = installments.batch_schedule(b)
    lines = []
    for d in b.details.all():
        m = d.membership
        lines.append(
            {
                "id": d.pk, "member": _person(d.member), "membership_id": m.pk, "entry_kind": d.entry_kind, "description": d.description,
                "product_id": d.product_id, "product_name": d.product.name if d.product_id else None,
                "requested_team_id": d.requested_team_id, "requested_team_name": d.requested_team.name if d.requested_team else None, "requested_position_name": d.requested_position.name if d.requested_position else None,
                "requested_jersey_number": d.requested_jersey_number, "number_state": invoicing.number_state(d),
                "base_price": d.base_price, "discount_amount": d.discount_amount, "price_override": d.price_override,
                "excluded": d.excluded, "price": d.price, "is_credit": d.is_credit, "credit_share": d.credit_share, "order_discount_share": d.order_discount_share, "payable": d.payable,
                "fee_amount": m.fee_amount, "early_payment_deadline": m.early_payment_deadline, "early_payment_discount": m.early_payment_discount,
                "next_installment": installments.next_unpaid(people.get(m.pk, [])), **pay.person(m),
            }
        )  # fmt: skip
    credit_total, credit_applied = invoicing.credits(b)
    order = invoicing.order_discounts(b)
    return {
        **_item(b, schedule), "season_id": b.season_id, "lines": lines, "order_discounts": order, "order_discount_total": sum((row["amount"] for row in order), Decimal("0.00")), "subtotal": invoicing.subtotal(b),
        "credit_total": credit_total, "credit_applied": credit_applied, "manual_discount_amount": b.manual_discount_amount, "manual_discount_note": b.manual_discount_note,
        "invoice_due_date": b.invoice_due_date, "invoice_sent_at": b.invoice_sent_at, "confirmation_sent_at": b.confirmation_sent_at, "cancelled_at": b.cancelled_at,
        "status_path": f"/join/status/{b.status_token}", "numbers_dropped": list(numbers_dropped),
        "payments": _payment_rows(b) if b.confirmed_at else [], "vouchers_enabled": vouchers_enabled, "early_bird": pay.early_bird(b), "installments": schedule,
        "legacy_invoice_history": b.legacy_invoice_history or None,
    }  # fmt: skip


@router.get("", response=RegistrationListOut, summary="Registrations (admin)", operation_id="list_registrations")
def list_registrations(request, status: str = "awaiting", q: str = "", payment: str = "all", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """``status``: ``awaiting`` (billing review, the default), ``confirmed``, ``cancelled`` or ``all``. ``q`` searches the contact and the invoice
    number. ``payment`` narrows to confirmed registrations by how far they are paid: ``unpaid``, ``partial``, ``open`` (unpaid + partial),
    ``paid``, ``installments`` or ``overdue`` (open and past the due date); ``all`` (the default) does not narrow."""
    _gate(request)
    qs = RegistrationBatch.objects.select_related("contact", "season").prefetch_related("details__membership__payments")
    if status == "awaiting":
        qs = qs.filter(confirmed_at__isnull=True, cancelled_at__isnull=True)
    elif status == "confirmed":
        qs = qs.filter(confirmed_at__isnull=False, cancelled_at__isnull=True)
    elif status == "cancelled":
        qs = qs.filter(cancelled_at__isnull=False)
    if q.strip():
        qs = qs.filter(Q(contact__first_name__icontains=q.strip()) | Q(contact__last_name__icontains=q.strip()) | Q(contact__email__icontains=q.strip()) | Q(invoice_number__icontains=q.strip()))
    if payment == "installments":
        qs = qs.exclude(installment_offsets=[])
    elif payment in {"unpaid", "partial", "open", "paid", "overdue"}:
        settled = Q(details__membership__fee_status__in=[ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED])
        qs = qs.filter(confirmed_at__isnull=False, cancelled_at__isnull=True).annotate(
            n_people=Count("details", distinct=True),
            n_settled=Count("details", filter=settled, distinct=True),
            n_started=Count("details", filter=Q(details__membership__fee_status__in=[ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.PARTIALLY_PAID]), distinct=True),
        )
        if payment == "paid":
            qs = qs.filter(n_settled=F("n_people")).exclude(Q(n_people=0) & ~Q(legacy_invoice_history={}))
        else:
            qs = qs.exclude(n_settled=F("n_people"))
            if payment == "unpaid":
                qs = qs.filter(n_started=0)
            elif payment == "partial":
                qs = qs.filter(n_started__gt=0)
            elif payment == "overdue":
                today = timezone.localdate()
                candidates = qs.filter(invoice_due_date__lt=today)
                late_batches = [
                    batch.pk for batch in candidates.exclude(installment_offsets=[]).iterator(chunk_size=100)
                    if any(row["late"] for row in installments.batch_schedule(batch, today=today)[0])
                ]
                qs = candidates.filter(Q(installment_offsets=[]) | Q(pk__in=late_batches))
    awaiting = RegistrationBatch.objects.filter(confirmed_at__isnull=True, cancelled_at__isnull=True).count()
    return {"count": qs.count(), "awaiting_count": awaiting, "results": [_item(b) for b in qs[offset : offset + limit]]}


@router.get("/{batch_id}", response=RegistrationDetail, summary="One registration with its lines (admin)", operation_id="get_registration")
def get_registration(request, batch_id: uuid.UUID):
    _gate(request)
    return _out(request, _batch(batch_id))


@router.patch("/{batch_id}", response=RegistrationDetail, summary="Edit the billing review (admin)", operation_id="review_registration")
def review(request, batch_id: uuid.UUID, payload: ReviewIn):
    """Only while awaiting: line price (or ``null`` for the computed one), exclude a line, manual discount + note. Each membership's fee follows.
    422 ``confirmed`` / ``cancelled`` afterwards."""
    _gate(request)
    data = payload.dict(exclude_unset=True)
    batch = invoicing.update_review(request.user, _batch(batch_id), lines=data.get("lines"), manual_discount_amount=data.get("manual_discount_amount"), manual_discount_note=data.get("manual_discount_note"))
    return _out(request, _batch(batch.pk))


@router.post("/{batch_id}/confirm", response=RegistrationDetail, summary="Confirm the invoice (admin)", operation_id="confirm_registration")
def confirm(request, batch_id: uuid.UUID, payload: ConfirmIn):
    """Numbers the invoice (``REG-<year>-0001``), sets the due date, rosters the requested teams and emails the contact that the registration is
    confirmed (one status mail; the invoice PDF is attached to it when ``send``). From now on the people show up in the Sign-ups queue."""
    _gate(request)
    batch = invoicing.confirm_invoice(request.user, _batch(batch_id), due_date=payload.due_date, send=payload.send)
    return _out(request, _batch(batch.pk), getattr(batch, "numbers_dropped", ()))


@router.post("/{batch_id}/cancel", response=RegistrationDetail, summary="Cancel the registration (admin)", operation_id="cancel_registration")
def cancel(request, batch_id: uuid.UUID):
    _gate(request)
    batch = invoicing.cancel_batch(request.user, _batch(batch_id))
    return _out(request, _batch(batch.pk))


@router.post("/{batch_id}/resend-confirmation", response=SentOut, summary="Resend the confirmation email (admin)", operation_id="resend_registration_confirmation")
def resend_confirmation(request, batch_id: uuid.UUID):
    _gate(request)
    return {"sent": notifications.send_confirmation(_batch(batch_id).pk)}


@router.post("/{batch_id}/resend-invoice", response=SentOut, summary="Resend the invoice email (admin)", operation_id="resend_registration_invoice")
def resend_invoice(request, batch_id: uuid.UUID):
    _gate(request)
    batch = _batch(batch_id)
    if not batch.confirmed_at:
        raise HttpError(409, "The invoice is not confirmed yet.")
    return {"sent": notifications.send_invoice(batch.pk)}


@router.get("/{batch_id}/invoice.pdf", summary="The invoice as PDF (admin)", operation_id="registration_invoice_pdf")
def invoice_pdf(request, batch_id: uuid.UUID):
    _gate(request)
    batch = _batch(batch_id)
    if not batch.confirmed_at:
        raise HttpError(409, "The invoice is not confirmed yet.")
    try:
        pdf = notifications.render_invoice_pdf(batch)
    except PDFUnavailable:
        raise HttpError(503, "PDF rendering is not available on this server.") from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{batch.invoice_number}.pdf"'
    return response


def _voucher(number: str | None):
    return shop_orders.find_voucher(number) if number else None


@router.get("/vouchers/lookup", response=VoucherLookupOut, summary="Look up a shop voucher (finance)", operation_id="lookup_registration_voucher")
def lookup_voucher(request, number: str):
    """What is left on a shop voucher, so the finance person can see it before taking money from it. 404 when the club has no shop."""
    _gate(request)
    if not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    v = shop_orders.find_voucher(number)
    return {"number": v.number, "amount": v.amount, "available": v.available_amount, "expiry_date": v.expiry_date, "status": vouchers.voucher_status(v)}


@router.post("/{batch_id}/payments", response={201: RegistrationDetail}, summary="Record a payment against the invoice (finance)", operation_id="record_registration_payment")
def record_payment(request, batch_id: uuid.UUID, payload: InvoicePaymentIn):
    """Full or partial. The amount fills the people on the invoice in order (each up to what they owe), or ``allocations`` says how much goes to whom
    (one entry = a payment for one person). ``method=voucher`` takes the amount from the shop voucher ``voucher_number``. 422 when the invoice is
    not confirmed, is cancelled, or the amount is more than it has open."""
    _gate(request)
    batch = _batch(batch_id)
    if payload.method == FeePayment.Method.VOUCHER and not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    pay.record(request.user, batch, payload.amount, method=payload.method, reference=payload.reference, paid_at=payload.paid_at, note=payload.note, voucher=_voucher(payload.voucher_number),
               allocations=[a.dict() for a in payload.allocations] if payload.allocations else None)  # fmt: skip
    return 201, _out(request, _batch(batch_id))


@router.post("/{batch_id}/mark-paid", response=RegistrationDetail, summary="Settle what the invoice still has open (finance)", operation_id="mark_registration_paid")
def mark_paid(request, batch_id: uuid.UUID, payload: MarkInvoicePaidIn):
    _gate(request)
    batch = _batch(batch_id)
    if payload.method == FeePayment.Method.VOUCHER and not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    pay.mark_paid(request.user, batch, method=payload.method, reference=payload.reference, paid_at=payload.paid_at, note=payload.note, voucher=_voucher(payload.voucher_number))
    return _out(request, _batch(batch_id))


@router.delete("/{batch_id}/payments/{payment_id}", response=RegistrationDetail, summary="Remove a mistaken payment (finance)", operation_id="delete_registration_payment")
def delete_payment(request, batch_id: uuid.UUID, payment_id: uuid.UUID):
    """A voucher payment gives its amount back to the voucher."""
    _gate(request)
    pay.delete_payment(request.user, _batch(batch_id), payment_id)
    return _out(request, _batch(batch_id))


@settings_router.get("/settings", response=RegistrationSettingsOut, summary="Registration settings (admin)", operation_id="get_registration_settings")
def get_settings(request):
    _settings_gate(request)
    s = config.get_settings()
    return {field: getattr(s, field) for field in config.FIELDS}


@settings_router.put("/settings", response=RegistrationSettingsOut, summary="Change the registration settings (admin)", operation_id="update_registration_settings")
def update_settings(request, payload: RegistrationSettingsIn):
    _settings_gate(request)
    s = config.update_settings(request.user, **payload.dict(exclude_unset=True))
    return {field: getattr(s, field) for field in config.FIELDS}
