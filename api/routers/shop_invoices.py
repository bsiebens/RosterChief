"""Shop invoices (``/shop/invoices``, ``/shop/orders/{id}/invoice``). Needs ``shop.manage_invoices``."""

import datetime
import uuid
from decimal import Decimal

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.routers.shop_orders import num, ref
from api.schemas_shop_orders import InvoiceCreateIn, InvoiceDetailOut, InvoiceItemOut, InvoiceListOut
from shop.models import Invoice, Order
from shop.services import invoices as svc
from shop.services import payments

router = Router(auth=AUTHENTICATORS)


def _gate(request):
    gate(request, access.require_manage_shop_invoices)


def _item(inv: Invoice) -> dict:
    o = inv.order
    paid = payments.amount_paid(o)
    due = max(Decimal("0"), o.total - paid)
    overdue = bool(inv.due_date and inv.due_date < timezone.localdate() and due > 0 and o.fulfillment_status != Order.FulfillmentStatus.CANCELLED)
    return {
        "id": inv.pk, "number": inv.number, "order": num(o), "purchaser": ref(o.purchaser), "issued_at": inv.issued_at, "due_date": inv.due_date, "total": o.total,
        "amount_paid": paid, "amount_due": due, "payment_status": o.payment_status, "is_overdue": overdue,
    }  # fmt: skip


def _detail(inv: Invoice) -> dict:
    o = inv.order
    return {**_item(inv), "billing_snapshot": inv.billing_snapshot, "lines": svc.lines_for(o), "discounts": [{"description": d.description, "amount": d.amount} for d in o.applied_discounts.all()]}


def _get(invoice_id) -> Invoice:
    inv = Invoice.objects.select_related("order__purchaser").filter(pk=invoice_id, order__kind=Order.Kind.SHOP).first()
    if inv is None:
        raise HttpError(404, "Not found.")
    return inv


@router.get("/invoices", response=InvoiceListOut, summary="List invoices", operation_id="list_shop_invoices")
def list_invoices(
    request, status: str | None = None, q: str | None = None, issued_from: datetime.date | None = None, issued_to: datetime.date | None = None,
    purchaser_id: uuid.UUID | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):  # fmt: skip
    _gate(request)
    qs = Invoice.objects.filter(order__kind=Order.Kind.SHOP).select_related("order__purchaser").order_by("-issued_at")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(order__number__icontains=q) | Q(order__purchaser__first_name__icontains=q) | Q(order__purchaser__last_name__icontains=q))
    if issued_from:
        qs = qs.filter(issued_at__date__gte=issued_from)
    if issued_to:
        qs = qs.filter(issued_at__date__lte=issued_to)
    if purchaser_id:
        qs = qs.filter(order__purchaser_id=purchaser_id)
    items = [_item(i) for i in qs]
    if status == "paid":
        items = [i for i in items if i["amount_due"] <= 0]
    elif status == "unpaid":
        items = [i for i in items if i["amount_due"] > 0]
    elif status == "overdue":
        items = [i for i in items if i["is_overdue"]]
    return {"count": len(items), "results": items[offset : offset + limit]}


@router.get("/invoices/{invoice_id}", response=InvoiceDetailOut, summary="One invoice", operation_id="get_shop_invoice")
def get_invoice(request, invoice_id: uuid.UUID):
    _gate(request)
    return _detail(_get(invoice_id))


@router.get("/invoices/{invoice_id}/pdf", summary="The invoice as PDF", operation_id="shop_invoice_pdf")
def pdf(request, invoice_id: uuid.UUID):
    _gate(request)
    inv = _get(invoice_id)
    try:
        data = svc.render_pdf(inv)
    except svc.PDFUnavailable:
        return HttpResponse('{"detail": "PDF rendering is not available.", "code": "pdf_unavailable"}', status=503, content_type="application/json")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{inv.number}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/invoices/{invoice_id}/recreate-pdf", response=InvoiceItemOut, summary="Rebuild the PDF of an invoice", operation_id="recreate_shop_invoice_pdf")
def recreate_pdf(request, invoice_id: uuid.UUID):
    """Drops the cached PDF and renders it again from the order as it is now (payments, lines, discounts); the invoice itself is unchanged."""
    _gate(request)
    inv = _get(invoice_id)
    try:
        svc.recreate_pdf(inv)
    except svc.PDFUnavailable:
        return HttpResponse('{"detail": "PDF rendering is not available.", "code": "pdf_unavailable"}', status=503, content_type="application/json")
    return _item(inv)


@router.post("/orders/{order_id}/invoice", response={201: InvoiceItemOut}, summary="Issue the invoice for an order", operation_id="create_shop_invoice")
def create(request, order_id: uuid.UUID, payload: InvoiceCreateIn):
    _gate(request)
    order = Order.objects.filter(pk=order_id, kind=Order.Kind.SHOP).first()
    if order is None:
        raise HttpError(404, "Not found.")
    inv = svc.create_invoice(order, due_date=payload.due_date)
    return 201, _item(_get(inv.pk))


@router.get("/orders/{order_id}/invoice", response=InvoiceItemOut, summary="The invoice of an order", operation_id="get_shop_order_invoice")
def for_order(request, order_id: uuid.UUID):
    _gate(request)
    inv = Invoice.objects.select_related("order__purchaser").filter(order_id=order_id, order__kind=Order.Kind.SHOP).first()
    if inv is None:
        raise HttpError(404, "Not found.")
    return _item(inv)
