"""Charge invoices (``/finance/charges``): invoices staff write for amounts that are not shop items (a fine, a late fee). Needs
``finance.manage_charges``; no feature gate. A charge is a draft until it is sent; see ``shop.services.charges`` for the rules."""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import Field, Query, Router, Schema
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_orders import payment_out
from api.schemas_shop_orders import Money, MoneyIn, PaymentIn, PaymentOut
from members.models import Member
from members.services import access
from members.services.errors import Invalid
from shop.models import Invoice, Order, Payment
from shop.services import charges as svc
from shop.services import invoices, payments
from shop.settings_models import ShopSettings

router = Router(auth=AUTHENTICATORS)

Kind = Order.Kind
Status = Literal["all", "draft", "open", "overdue", "paid", "cancelled"]


def _gate(request):
    access.require_charges(request.user)  # 403 first
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


class MemberRef(Schema):
    id: uuid.UUID
    name: str


class ChargeLineIn(Schema):
    description: str = Field(max_length=255)
    quantity: int = Field(1, ge=1, le=999)
    unit_price: MoneyIn = Field(gt=0)


class ChargeIn(Schema):
    member_id: uuid.UUID
    lines: list[ChargeLineIn] = Field(min_length=1, max_length=svc.MAX_LINES)
    #: The due date the invoice gets when it is sent; empty means the shop's default terms.
    due_date: datetime.date | None = None
    notes: str = Field("", max_length=2000)


class ChargePatch(Schema):
    member_id: uuid.UUID | None = None
    lines: list[ChargeLineIn] | None = Field(None, min_length=1, max_length=svc.MAX_LINES)
    due_date: datetime.date | None = None  # null clears it
    notes: str | None = Field(None, max_length=2000)


class SendIn(Schema):
    due_date: datetime.date | None = None


class ChargeItemOut(Schema):
    id: uuid.UUID
    status: str
    #: The invoice number once sent; a draft has none yet.
    number: str | None
    member: MemberRef
    summary: str
    total: Money
    amount_paid: Money
    amount_due: Money
    due_date: datetime.date | None
    issued_at: datetime.datetime | None
    created: datetime.datetime
    reminders_sent: int


class ChargeSummaryOut(Schema):
    drafts: int
    open: int
    overdue: int
    paid: int
    cancelled: int
    open_amount: Money
    overdue_amount: Money


class ChargeListOut(Schema):
    count: int
    results: list[ChargeItemOut]
    summary: ChargeSummaryOut


class ChargeLineOut(Schema):
    id: uuid.UUID
    description: str
    quantity: int
    unit_price: Money
    line_total: Money


class ChargeDetailOut(ChargeItemOut):
    lines: list[ChargeLineOut]
    notes: str
    payments: list[PaymentOut]
    last_reminder_at: datetime.datetime | None
    #: Draft: lines, member, due date and note can change; it can be sent or deleted.
    is_draft: bool
    can_cancel: bool
    can_remind: bool
    can_record_payment: bool
    #: When the invoice is sent now, the member (and for a child the guardians) get it at this address.
    recipient_email: str


class ChargeSettingsOut(Schema):
    reminders_enabled: bool
    #: Days past the due date before the first reminder, then one every ``repeat_days``, at most ``max_reminders``.
    after_days: int
    repeat_days: int
    max_reminders: int
    #: Default payment term (days after sending) for an invoice without its own due date.
    invoice_due_days: int


class ChargeSettingsIn(Schema):
    reminders_enabled: bool | None = None
    after_days: int | None = Field(None, ge=0, le=90)
    repeat_days: int | None = Field(None, ge=1, le=90)
    max_reminders: int | None = Field(None, ge=0, le=20)
    invoice_due_days: int | None = Field(None, ge=0, le=365)


def _base():
    paid = Coalesce(Sum("payments__amount", filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED)), Value(Decimal("0")), output_field=DecimalField())
    return Order.objects.filter(kind=Kind.CHARGE).select_related("purchaser", "invoice").annotate(paid=paid)


def _invoice(order):
    try:
        return order.invoice
    except Invoice.DoesNotExist:
        return None


def _summary_text(order) -> str:
    lines = list(order.lines.order_by("created").values_list("description", flat=True)[:2])
    count = order.lines.count()
    first = lines[0] if lines else ""
    return first if count <= 1 else f"{first} +{count - 1}"


def _item(order, today: datetime.date) -> dict:
    invoice = _invoice(order)
    paid = getattr(order, "paid", None)
    paid = payments.amount_paid(order) if paid is None else paid
    return {
        "id": order.pk, "status": svc.status_for(order, invoice, today), "number": invoice.number if invoice else None,
        "member": {"id": order.purchaser_id, "name": str(order.purchaser)}, "summary": _summary_text(order), "total": order.total,
        "amount_paid": paid, "amount_due": max(Decimal("0"), order.total - paid), "due_date": invoice.due_date if invoice else order.due_date,
        "issued_at": invoice.issued_at if invoice else None, "created": order.created, "reminders_sent": invoice.reminders_sent if invoice else 0,
    }  # fmt: skip


def _detail(order_id) -> dict:
    order = _base().get(pk=order_id)
    today = timezone.localdate()
    item = _item(order, today)
    invoice = _invoice(order)
    draft = item["status"] == "draft"
    cancelled = item["status"] == "cancelled"
    paid_in_full = item["status"] == "paid"
    return {
        **item,
        "lines": [{"id": ln.pk, "description": ln.description, "quantity": ln.quantity, "unit_price": ln.unit_price, "line_total": ln.line_total} for ln in order.lines.order_by("created")],
        "notes": order.notes,
        "payments": [payment_out(p) for p in order.payments.select_related("voucher", "recorded_by").order_by("created")],
        "last_reminder_at": invoice.last_reminder_at if invoice else None,
        "is_draft": draft,
        "can_cancel": not draft and not cancelled and item["amount_paid"] <= 0,
        "can_remind": not draft and not cancelled and not paid_in_full,
        "can_record_payment": not draft and not cancelled and not paid_in_full,
        "recipient_email": order.purchaser.contact_email,
    }


def _get(charge_id) -> Order:
    order = Order.objects.filter(pk=charge_id, kind=Kind.CHARGE).select_related("purchaser").first()
    if order is None:
        raise HttpError(404, "Not found.")
    return order


def _member(member_id) -> Member:
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise Invalid("Unknown member.", {"member_id": ["Choose a member."]})
    return member


def _lines(lines) -> list[svc.ChargeLine]:
    return [svc.ChargeLine(description=ln.description, quantity=ln.quantity, unit_price=ln.unit_price) for ln in lines]


@router.get("/settings", response=ChargeSettingsOut, summary="Reminder settings for overdue charges", operation_id="get_charge_settings")
def get_settings(request):
    _gate(request)
    return _settings_out()


def _settings_out() -> dict:
    s = ShopSettings.current()
    return {
        "reminders_enabled": s.charge_reminders_enabled, "after_days": s.charge_reminder_after_days, "repeat_days": s.charge_reminder_repeat_days,
        "max_reminders": s.charge_reminder_max, "invoice_due_days": s.invoice_due_days,
    }  # fmt: skip


@router.put("/settings", response=ChargeSettingsOut, summary="Change the reminder settings", operation_id="update_charge_settings")
def put_settings(request, payload: ChargeSettingsIn):
    _gate(request)
    s = ShopSettings.current()
    data = payload.dict(exclude_unset=True)
    mapping = {"reminders_enabled": "charge_reminders_enabled", "after_days": "charge_reminder_after_days", "repeat_days": "charge_reminder_repeat_days",
               "max_reminders": "charge_reminder_max", "invoice_due_days": "invoice_due_days"}  # fmt: skip
    for key, field in mapping.items():
        if data.get(key) is not None:
            setattr(s, field, data[key])
    s.save()
    return _settings_out()


@router.get("", response=ChargeListOut, summary="List charge invoices", operation_id="list_charges")
def list_charges(request, status: Status = "all", q: str = "", member_id: uuid.UUID | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    today = timezone.localdate()
    base = _base()
    open_q = Q(invoice__isnull=False, payment_status__in=[Order.PaymentStatus.PENDING, Order.PaymentStatus.PARTIALLY_PAID]) & ~Q(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    overdue_q = open_q & Q(invoice__due_date__lt=today)
    totals = base.aggregate(
        drafts=Count("pk", filter=Q(invoice__isnull=True) & ~Q(fulfillment_status=Order.FulfillmentStatus.CANCELLED)),
        open=Count("pk", filter=open_q), overdue=Count("pk", filter=overdue_q),
        paid=Count("pk", filter=Q(invoice__isnull=False, payment_status=Order.PaymentStatus.PAID) & ~Q(fulfillment_status=Order.FulfillmentStatus.CANCELLED)),
        cancelled=Count("pk", filter=Q(fulfillment_status=Order.FulfillmentStatus.CANCELLED)),
        open_total=Sum("total", filter=open_q), overdue_total=Sum("total", filter=overdue_q),
    )  # fmt: skip
    open_paid = base.filter(open_q).aggregate(v=Sum("payments__amount", filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED)))["v"] or Decimal("0")
    overdue_paid = base.filter(overdue_q).aggregate(v=Sum("payments__amount", filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED)))["v"] or Decimal("0")
    qs = base
    if status == "draft":
        qs = qs.filter(invoice__isnull=True).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    elif status == "open":
        qs = qs.filter(open_q)
    elif status == "overdue":
        qs = qs.filter(overdue_q)
    elif status == "paid":
        qs = qs.filter(invoice__isnull=False, payment_status=Order.PaymentStatus.PAID).exclude(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    elif status == "cancelled":
        qs = qs.filter(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    if member_id:
        qs = qs.filter(purchaser_id=member_id)
    if q.strip():
        qs = qs.filter(
            Q(purchaser__first_name__icontains=q.strip()) | Q(purchaser__last_name__icontains=q.strip()) | Q(invoice__number__icontains=q.strip())
            | Q(lines__description__icontains=q.strip())
        ).distinct()  # fmt: skip
    qs = qs.order_by("-created")
    return {
        "count": qs.count(), "results": [_item(o, today) for o in qs[offset : offset + limit]],
        "summary": {
            "drafts": totals["drafts"], "open": totals["open"], "overdue": totals["overdue"], "paid": totals["paid"], "cancelled": totals["cancelled"],
            "open_amount": (totals["open_total"] or Decimal("0")) - open_paid, "overdue_amount": (totals["overdue_total"] or Decimal("0")) - overdue_paid,
        },
    }  # fmt: skip


@router.post("", response={201: ChargeDetailOut}, summary="Write a charge invoice (a draft)", operation_id="create_charge")
def create(request, payload: ChargeIn):
    _gate(request)
    order = svc.create_draft(request.user, member=_member(payload.member_id), lines=_lines(payload.lines), due_date=payload.due_date, notes=payload.notes)
    return 201, _detail(order.pk)


@router.get("/{charge_id}", response=ChargeDetailOut, summary="One charge invoice", operation_id="get_charge")
def get_charge(request, charge_id: uuid.UUID):
    _gate(request)
    _get(charge_id)
    return _detail(charge_id)


@router.put("/{charge_id}", response=ChargeDetailOut, summary="Change a draft", operation_id="update_charge")
def update(request, charge_id: uuid.UUID, payload: ChargePatch):
    _gate(request)
    order = _get(charge_id)
    data = payload.dict(exclude_unset=True)
    svc.update_draft(
        request.user, order, member=_member(data["member_id"]) if data.get("member_id") else None,
        lines=_lines(payload.lines) if payload.lines is not None else None, due_date=data["due_date"] if "due_date" in data else "keep", notes=data.get("notes"),
    )  # fmt: skip
    return _detail(charge_id)


@router.delete("/{charge_id}", response={204: None}, summary="Delete a draft", operation_id="delete_charge")
def delete(request, charge_id: uuid.UUID):
    _gate(request)
    svc.delete_draft(_get(charge_id))
    return 204, None


@router.post("/{charge_id}/send", response=ChargeDetailOut, summary="Send the invoice to the member", operation_id="send_charge")
def send(request, charge_id: uuid.UUID, payload: SendIn):
    _gate(request)
    svc.send(request.user, _get(charge_id), due_date=payload.due_date)
    return _detail(charge_id)


@router.post("/{charge_id}/cancel", response=ChargeDetailOut, summary="Cancel a sent invoice that has no payments", operation_id="cancel_charge")
def cancel(request, charge_id: uuid.UUID):
    _gate(request)
    svc.cancel(request.user, _get(charge_id))
    return _detail(charge_id)


@router.post("/{charge_id}/remind", response=ChargeDetailOut, summary="Send a reminder now", operation_id="remind_charge")
def remind(request, charge_id: uuid.UUID):
    _gate(request)
    order = _get(charge_id)
    invoice = _invoice(order)
    if invoice is None:
        raise HttpError(409, "Send the invoice first.")
    svc.send_reminder(invoice)
    return _detail(charge_id)


@router.post("/{charge_id}/payments", response={201: PaymentOut}, summary="Record a payment", operation_id="record_charge_payment")
def record_payment(request, charge_id: uuid.UUID, payload: PaymentIn):
    _gate(request)
    order = _get(charge_id)
    p = svc.record_payment(
        order, amount=payload.amount, method=payload.method, reference=payload.reference, note=payload.note, paid_at=payload.paid_at, actor=request.user
    )
    return 201, payment_out(Payment.objects.select_related("voucher", "recorded_by").get(pk=p.pk))


@router.delete("/{charge_id}/payments/{payment_id}", response={204: None}, summary="Delete a payment", operation_id="delete_charge_payment")
def delete_payment(request, charge_id: uuid.UUID, payment_id: uuid.UUID):
    _gate(request)
    order = _get(charge_id)
    payment = order.payments.filter(pk=payment_id).first()
    if payment is None:
        raise HttpError(404, "Not found.")
    payments.delete_payment(payment)
    return 204, None


@router.get("/{charge_id}/pdf", summary="The invoice as PDF", operation_id="charge_pdf")
def pdf(request, charge_id: uuid.UUID):
    _gate(request)
    invoice = _invoice(_get(charge_id))
    if invoice is None:
        raise HttpError(409, "Send the invoice first.")
    try:
        data = invoices.render_pdf(invoice)
    except invoices.PDFUnavailable:
        return HttpResponse('{"detail": "PDF rendering is not available.", "code": "pdf_unavailable"}', status=503, content_type="application/json")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
    return response
