"""The central invoices list (``/finance/invoices``): registration invoices, shop invoices and charge invoices (fines and other costs) in one place.

Each kind is only listed to someone allowed to see it: registrations need ``registration.manage_finance``, shop invoices the shop feature and
``shop.manage_invoices``, charges ``finance.manage_charges``. A row links to the screen of its own kind; nothing is edited here."""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers import finance as finance_router
from api.routers import registrations as registrations_router
from api.routers.finance_charges import open_filters, order_stats
from api.schemas_shop_orders import Money
from members.services import access
from members.services.errors import NotAllowed
from shop.models import Order, Payment
from shop.services import charges as charge_svc

router = Router(auth=AUTHENTICATORS)

Kind = Order.Kind
#: ``orders`` is shop and charge invoices together: everything that is not a registration (the Payments page lists those itself).
InvoiceType = Literal["all", "registration", "shop", "charge", "orders"]
Status = Literal["all", "draft", "open", "overdue", "paid", "cancelled"]
ZERO = Decimal("0")


class MemberRef(Schema):
    id: uuid.UUID
    name: str


class InvoiceRowOut(Schema):
    type: str
    #: The id of the registration or order the row opens (its own screen: ``href``).
    id: uuid.UUID
    number: str | None
    member: MemberRef | None
    summary: str
    status: str
    total: Money
    amount_paid: Money
    amount_due: Money
    due_date: datetime.date | None
    issued_at: datetime.datetime | None
    href: str


class TypeSummaryOut(Schema):
    drafts: int
    open: int
    overdue: int
    paid: int
    cancelled: int
    open_amount: Money
    overdue_amount: Money
    invoiced_amount: Money = ZERO
    received_amount: Money = ZERO


class InvoiceListOut(Schema):
    count: int
    results: list[InvoiceRowOut]
    #: The kinds this person may see, and the counts and amounts of each (``open`` includes the overdue ones).
    types: list[str]
    summary: dict[str, TypeSummaryOut]


def _allowed(request) -> list[str]:
    user = request.user
    kinds = []
    if access.can_manage_finance(user):
        kinds.append("registration")
    if request.features.has("shop") and access.can_manage_shop_invoices(user):
        kinds.append("shop")
    if access.can_manage_charges(user):
        kinds.append("charge")
    if not kinds:
        raise NotAllowed("You are not allowed to see invoices.")
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    return kinds


# ------------------------------------------------------------------------------------------------- orders (shop and charges)


def _orders(kind, today):
    paid = Coalesce(Sum("payments__amount", filter=Q(payments__status=Payment.PaymentStatus.CONFIRMED)), Value(ZERO), output_field=DecimalField())
    qs = Order.objects.filter(kind=kind).select_related("purchaser", "invoice").annotate(paid=paid)
    if kind == Kind.SHOP:
        qs = qs.filter(invoice__isnull=False)  # a shop order without an invoice is not one
    return qs


def _orders_filtered(kind, status, q, today):
    qs = _orders(kind, today)
    open_q, overdue_q = open_filters(today)
    cancelled = Q(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
    if status == "draft":
        qs = qs.filter(invoice__isnull=True).exclude(cancelled)
    elif status == "open":
        qs = qs.filter(open_q)
    elif status == "overdue":
        qs = qs.filter(overdue_q)
    elif status == "paid":
        qs = qs.filter(invoice__isnull=False, payment_status=Order.PaymentStatus.PAID).exclude(cancelled)
    elif status == "cancelled":
        qs = qs.filter(cancelled)
    term = q.strip()
    if term:
        match = Q(purchaser__first_name__icontains=term) | Q(purchaser__last_name__icontains=term) | Q(invoice__number__icontains=term) | Q(number__icontains=term)
        match |= Q(lines__description__icontains=term) if kind == Kind.CHARGE else Q(lines__product__name__icontains=term)
        qs = qs.filter(match).distinct()
    return qs


def _order_summary(order, kind) -> str:
    lines = list(order.lines.all())
    first = (lines[0].description if kind == Kind.CHARGE else lines[0].product.name) if lines else ""
    return first if len(lines) <= 1 else f"{first} +{len(lines) - 1}"


def _order_row(order, today) -> dict:
    invoice = getattr(order, "invoice", None)
    kind = order.kind
    status = charge_svc.status_for(order, invoice, today)
    paid = order.paid
    return {
        "type": "charge" if kind == Kind.CHARGE else "shop", "id": order.pk, "number": invoice.number if invoice else None,
        "member": {"id": order.purchaser_id, "name": str(order.purchaser)}, "summary": _order_summary(order, kind),
        "status": "open" if status == "sent" else status, "total": order.total, "amount_paid": paid, "amount_due": max(ZERO, order.total - paid),
        "due_date": invoice.due_date if invoice else order.due_date, "issued_at": invoice.issued_at if invoice else None,
        "href": f"/finance/charges/{order.pk}" if kind == Kind.CHARGE else f"/shop/orders/{order.pk}",
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------- registrations


def _batches(status, q):
    """Confirmed (invoiced) registrations narrowed like the registrations list. A draft is not a thing here."""
    if status == "draft":
        return None
    if status in ("open", "overdue", "paid"):
        qs = registrations_router.filtered_batches("confirmed", q, status)
    elif status == "cancelled":
        qs = registrations_router.filtered_batches("cancelled", q, "all")
    else:
        qs = registrations_router.filtered_batches("all", q, "all")
    return qs.filter(invoice_number__isnull=False)


_REGISTRATION_STATUS = {"unpaid": "open", "partial": "partially_paid", "paid": "paid", "overdue": "overdue", None: "cancelled"}


def _batch_row(batch) -> dict:
    item = registrations_router._item(batch)
    contact = batch.contact
    status = "cancelled" if batch.cancelled_at else _REGISTRATION_STATUS.get(item["payment_status"], "open")
    paid, due = Decimal(str(item["amount_paid"])), Decimal(str(item["balance"]))
    return {
        "type": "registration", "id": batch.pk, "number": batch.invoice_number, "member": {"id": contact.pk, "name": str(contact)} if contact else None,
        "summary": batch.season.name, "status": status, "total": Decimal(str(item["total"])), "amount_paid": paid, "amount_due": max(ZERO, due),
        "due_date": batch.invoice_due_date, "issued_at": batch.confirmed_at, "href": f"/finance/{batch.pk}",
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------- the list


@router.get("", response=InvoiceListOut, summary="Every invoice the person may see, in one list", operation_id="list_finance_invoices")
def list_invoices(request, type: InvoiceType = "all", status: Status = "all", q: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    kinds = _allowed(request)
    today = timezone.localdate()
    wanted = [k for k in kinds if type in ("all", k) or (type == "orders" and k in ("shop", "charge"))]
    want = offset + limit
    candidates, count = [], 0
    for kind in wanted:
        if kind == "registration":
            qs = _batches(status, q)
            if qs is None:
                continue
            count += qs.count()
            candidates += [(batch.confirmed_at or batch.submitted_at, "registration", batch) for batch in qs.order_by("-confirmed_at", "-submitted_at")[:want]]
        else:
            order_kind = Kind.CHARGE if kind == "charge" else Kind.SHOP
            qs = _orders_filtered(order_kind, status, q, today)
            count += qs.count()
            candidates += [(order.invoice.issued_at if getattr(order, "invoice", None) else order.created, kind, order) for order in qs.order_by("-created")[:want]]
    candidates.sort(key=lambda c: c[0], reverse=True)
    page = candidates[offset : offset + limit]
    rows = [_batch_row(obj) if kind == "registration" else _order_row(obj, today) for _when, kind, obj in page]
    return {"count": count, "results": rows, "types": kinds, "summary": _summaries(request, kinds, today)}


def _summaries(request, kinds, today) -> dict:
    out = {}
    for kind in kinds:
        if kind == "registration":
            count = lambda status, payment: registrations_router.filtered_batches(status, "", payment).filter(invoice_number__isnull=False).count()  # noqa: E731
            totals = finance_router.summary(request, season_id=None, from_=None, to=None, q="")
            out[kind] = {
                "drafts": 0, "open": count("confirmed", "open"), "overdue": count("confirmed", "overdue"), "paid": count("confirmed", "paid"), "cancelled": count("cancelled", "all"),
                "open_amount": totals["open"], "overdue_amount": totals["overdue"],
            }  # fmt: skip
        else:
            out[kind] = order_stats(_orders(Kind.CHARGE if kind == "charge" else Kind.SHOP, today), today)
    return out
