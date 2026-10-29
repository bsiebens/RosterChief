"""Management API: shop orders (API.md §17).

The JSON counterpart of the ``/manage/shop/orders/*`` screens (management/views.py ``Order*``):

* list (filters, cursor paging), KPI summary and the production-product choices, detail;
* single-order actions: mark paid, mark ready for pickup, mark delivered, cancel;
* bulk mark paid and bulk mark ready for pickup: Celery jobs (``202`` + ``JobOut``);
* the ready-for-pickup queue (merchandise fully back from production);
* line edit (``PATCH``, JSON) and mark received; payments: add, edit (method and reference), delete;
* the order's invoice as a PDF download;
* the manufacturer production workflow: ``POST export-production`` is a job (it builds the
  spreadsheet and marks the lines in production; the job's ``result`` carries a one-time ``token``
  fetched with ``GET export-production/{token}``, a file download authenticated by the bearer token);
  ``POST reprint-production`` is a direct file download of what is currently in production
  (nothing is marked); ``GET``/``POST receive-production`` read and enter the counts that arrived
  back (FIFO per product and variant), synchronously.

Who may do what is the web's rule: ``can_manage_shop`` (a club admin or a shop manager) with the ``shop``
feature on (``404 feature_disabled`` otherwise). The logic is shared with the web views in
shop.services.orders / payments / production; the ``@job_task`` functions are in shop/tasks.py.
"""

import uuid
from typing import Annotated, Literal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.auth import require_club_member
from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.jobs import start_job
from api.core.pagination import paginate
from api.core.schemas import ErrorOut, JobOut
from api.panel.guards import require
from api.panel.schemas import shop_orders as s
from api.protected.services import club_features
from club.services.access import can_manage_shop
from management.forms import AddPaymentForm, OrderLineEditForm, PaymentEditForm
from management.shop_export import build_production_export, pop_production_export
from shop.models import Invoice, Order, OrderLine, Payment
from shop.services.inventory import InventoryError
from shop.services.invoices import ShopInvoicePDFError, render_invoice_pdf
from shop.services.orders import (
    cancel_order,
    filter_orders,
    mark_delivered,
    mark_paid,
    mark_ready_for_pickup,
    merchandise_products,
    production_products,
    ready_for_pickup_orders,
    remove_payment,
    save_order_line,
)
from shop.services.payments import PaymentError, amount_due, amount_paid, record_payment
from shop.services.production import in_production_lines, mark_line_received, pending_production_lines, production_receiving_summary, receive_production
from shop.services.stats import order_kpis
from shop.tasks import bulk_mark_paid_job, bulk_mark_ready_for_pickup_job, export_production_job

router = Router(tags=["panel: shop orders"])

PREFIX = "/manage/shop/orders"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- helpers -----------------------------------------------------------------------------------


def _club(request):
    """The club on this host for someone who runs its shop (ShopManagerRequiredMixin: the feature
    first, then ``can_manage_shop``, which also lets a shop manager without any other role in)."""
    club = require_club_member(request)
    if not club_features(club)["shop"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    require(request, club, can_manage_shop)
    return club


def _order(club, order_id) -> Order:
    return get_object_or_404(Order.objects.filter(club=club).select_related("purchaser"), pk=order_id)


def _item(order: Order) -> dict:
    has_lines = getattr(order, "has_production_line_items", None)
    return {
        "id": order.pk,
        "number": order.number,
        "purchaser_id": order.purchaser_id,
        "purchaser_name": str(order.purchaser),
        "payment_status": order.payment_status,
        "fulfillment_status": order.fulfillment_status,
        "production_status": order.production_status,
        "has_production_lines": order.has_production_lines if has_lines is None else has_lines,
        "total": order.total,
        "is_closed": order.is_closed,
        "created": order.created,
    }


def _line_out(line: OrderLine) -> dict:
    return {
        "id": line.pk,
        "product_id": line.product_id,
        "product_name": line.product.name,
        "is_merchandise": line.product.product_type == line.product.ProductType.MERCHANDISE,
        "personalization_enabled": line.product.personalization_enabled,
        "variant_id": line.variant_id,
        "variant_name": line.variant.name if line.variant_id else "",
        "quantity": line.quantity,
        "unit_price": line.unit_price,
        "line_total": line.line_total,
        "beneficiary_id": line.beneficiary_id,
        "beneficiary_name": str(line.beneficiary) if line.beneficiary_id else "",
        "team_id": line.team_id,
        "team_name": str(line.team) if line.team_id else "",
        "personalization_number": line.personalization_number,
        "personalization_name": line.personalization_name,
        "production_status": line.production_status,
    }


def _payment_out(payment: Payment) -> dict:
    return {
        "id": payment.pk,
        "amount": payment.amount,
        "method": payment.method,
        "status": payment.status,
        "reference": payment.reference,
        "paid_at": payment.paid_at,
        "voucher_id": payment.voucher_id,
        "voucher_number": payment.voucher.number if payment.voucher_id else "",
    }


def _detail(order: Order) -> dict:
    """``order`` is re-read first, so a status changed by the action just run is what comes back."""
    order = Order.objects.select_related("purchaser").get(pk=order.pk)
    invoice = Invoice.objects.filter(order=order).first()
    lines = order.order_items.select_related("product", "variant", "beneficiary", "team").order_by("product__name", "pk")
    payments = order.payments.select_related("voucher").order_by("paid_at", "pk")
    discounts = order.applied_discounts.select_related("discount").order_by("pk")
    return {
        **_item(order),
        "amount_paid": amount_paid(order),
        "amount_due": amount_due(order),
        "pickup_instructions": order.pickup_instructions,
        "modified": order.modified,
        "invoice": {"id": invoice.pk, "number": invoice.number, "issued_at": invoice.issued_at, "due_date": invoice.due_date} if invoice else None,
        "lines": [_line_out(line) for line in lines],
        "payments": [_payment_out(payment) for payment in payments],
        "applied_discounts": [{"id": d.pk, "discount_id": d.discount_id, "name": d.discount.name, "description": d.description, "amount": d.amount} for d in discounts],
    }


def _acting(request, order_id, action, *, status: int = 200):
    """Look the order up and run ``action(order)`` inside the idempotency claim, answering the
    refreshed detail."""
    club = _club(request)

    def work():
        order = _order(club, order_id)
        action(order)
        return status, _detail(order)

    return idempotent(request, work)


def _payment_error(error: PaymentError) -> ApiError:
    return ApiError(422, "payment_error", str(error))


def _file(content: bytes, content_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _merchandise(club, product_ids):
    products = merchandise_products(club).filter(pk__in=product_ids)
    if not products.exists():
        raise ApiError(422, "validation_error", fields={"product_ids": [str(_("Select at least one merchandise product."))]})
    return products


# --- list, summary, detail ---------------------------------------------------------------------


@router.get(PREFIX, response={200: s.OrderListOut, **ERRORS}, summary="List orders")
def list_orders(
    request,
    payment_status: Literal["pending", "partially_paid", "paid", "refunded"] | None = None,
    fulfillment_status: Literal["not_ready", "ready_for_pickup", "delivered", "cancelled"] | None = None,
    show: Annotated[Literal["open", "all"] | None, Query(description="Default `open`: closed orders (delivered and paid) are hidden. Ignored once a status filter is set.")] = "open",
    q: Annotated[str | None, Query(description="Purchaser's first or last name, a family name, or the order number.")] = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Newest first, cursor-paged."""
    club = _club(request)
    orders = filter_orders(club, payment_status=payment_status or "", fulfillment_status=fulfillment_status or "", show_all=show == "all", search=q or "")
    page = paginate(orders, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_item(order) for order in page.items], "next_cursor": page.next_cursor}


@router.get(PREFIX + "/summary", response={200: s.ShopOrdersSummaryOut, **ERRORS}, summary="Order KPIs and production products")
def orders_summary(request):
    """The strip above the web list (whole shop, whatever the list filters) and every merchandise product with its pending and in-production line counts."""
    club = _club(request)
    products = [{"id": p.pk, "name": p.name, "pending_count": p.pending_count, "in_production_count": p.in_production_count} for p in production_products(club)]
    return {**order_kpis(club), "production_products": products}


@router.get(PREFIX + "/ready-for-pickup", response={200: s.ReadyOrderListOut, **ERRORS}, summary="Orders whose merchandise is back from production")
def list_ready_for_pickup(request, cursor: str | None = None, limit: int | None = None):
    """Orders not yet marked ready whose merchandise is all received; offer them to `bulk/mark-ready-for-pickup`. Oldest first."""
    club = _club(request)
    page = paginate(ready_for_pickup_orders(club), sort_field="created", cursor=cursor, limit=limit)
    for order in page.items:
        order.has_production_line_items = True  # received production implies merchandise lines
    return {"items": [{**_item(order), "has_personalized_lines": order.has_personalized_lines} for order in page.items], "next_cursor": page.next_cursor}


@router.get(PREFIX + "/{uuid:order_id}", response={200: s.OrderDetailOut, **ERRORS}, summary="Order detail")
def order_detail(request, order_id: uuid.UUID):
    """Lines, payments, applied discounts, what is due and the invoice. Other clubs' orders are 404."""
    club = _club(request)
    return _detail(_order(club, order_id))


# --- single-order actions ----------------------------------------------------------------------


@router.post(PREFIX + "/{uuid:order_id}/mark-paid", response={200: s.OrderDetailOut, **ERRORS, **INVALID, **CONFLICT}, summary="Settle what is still due")
def order_mark_paid(request, order_id: uuid.UUID, payload: s.MarkPaidIn):
    """Records one payment for the remaining balance (a voucher that already covered part is not charged twice).
    `409 nothing_to_collect` when the order is already fully paid. Honours `Idempotency-Key`."""

    def act(order):
        if mark_paid(order, method=payload.method, reference=payload.reference) is None:
            raise ApiError(409, "nothing_to_collect", _("Order %(number)s is already fully paid.") % {"number": order.number})

    return _acting(request, order_id, act)


@router.post(PREFIX + "/{uuid:order_id}/mark-ready-for-pickup", response={200: s.OrderDetailOut, **ERRORS}, summary="Mark ready for pickup")
def order_mark_ready(request, order_id: uuid.UUID, payload: s.MarkReadyIn):
    """Whatever its payment state. Keeps the instructions on the order and notifies the purchaser. Honours `Idempotency-Key`."""
    return _acting(request, order_id, lambda order: mark_ready_for_pickup(order, pickup_instructions=payload.pickup_instructions))


@router.post(PREFIX + "/{uuid:order_id}/mark-delivered", response={200: s.OrderDetailOut, **ERRORS}, summary="Mark delivered")
def order_mark_delivered(request, order_id: uuid.UUID):
    """A plain status set: it does not require the order to be paid first."""
    return _acting(request, order_id, mark_delivered)


@router.post(PREFIX + "/{uuid:order_id}/cancel", response={200: s.OrderDetailOut, **ERRORS}, summary="Cancel an order")
def order_cancel(request, order_id: uuid.UUID):
    """Cancels it and puts what it took back in stock. Payments are untouched (a cancelled but unrefunded order is valid)."""
    return _acting(request, order_id, lambda order: cancel_order(order, user=request.user))


# --- bulk actions (jobs) -----------------------------------------------------------------------


@router.post(PREFIX + "/bulk/mark-paid", response={202: JobOut, **ERRORS, **INVALID}, summary="Mark many orders paid (job)")
def bulk_mark_paid(request, payload: s.BulkMarkPaidIn):
    """Each order settles what is still due with the shared `method` (no reference). Runs as a job; its `result` is
    `{updated}`. Ids of other clubs and cancelled orders are ignored, orders with nothing due are skipped. Honours `Idempotency-Key`."""
    _club(request)
    ids = [str(pk) for pk in dict.fromkeys(payload.order_ids)]
    return idempotent(request, lambda: (202, start_job(request, bulk_mark_paid_job, ids, payload.method)))


@router.post(PREFIX + "/bulk/mark-ready-for-pickup", response={202: JobOut, **ERRORS, **INVALID}, summary="Mark many orders ready for pickup (job)")
def bulk_mark_ready(request, payload: s.BulkMarkReadyIn):
    """One shared `pickup_instructions`; each purchaser is notified about their own order. Runs as a job; its `result` is
    `{updated}`. Ids of other clubs and cancelled orders are ignored. Honours `Idempotency-Key`."""
    _club(request)
    ids = [str(pk) for pk in dict.fromkeys(payload.order_ids)]
    return idempotent(request, lambda: (202, start_job(request, bulk_mark_ready_for_pickup_job, ids, payload.pickup_instructions)))


# --- lines -------------------------------------------------------------------------------------


def _line(club, order_id, line_id) -> OrderLine:
    return get_object_or_404(OrderLine.objects.filter(order__club=club, order__pk=order_id).select_related("order", "product", "variant"), pk=line_id)


@router.patch(PREFIX + "/{uuid:order_id}/lines/{uuid:line_id}", response={200: s.OrderDetailOut, **ERRORS, **INVALID, **CONFLICT}, summary="Correct an order line")
def update_line(request, order_id: uuid.UUID, line_id: uuid.UUID, payload: s.LineUpdateIn):
    """Fixes a mistake after the fact: size (`variant_id`), beneficiary, quantity, personalization, or the production status by hand.
    Only the fields sent change; null clears the variant, beneficiary and personalization. The product is not editable. Unit price and total are
    recomputed from the quantity and the variant, stock follows, and the order's total, payment status and production rollup are kept in sync.
    `409 not_enough_stock` when the new size lacks stock."""
    club = _club(request)
    line = _line(club, order_id, line_id)
    old_variant_id = line.variant_id
    values = {
        "variant_id": line.variant_id,
        "beneficiary_id": line.beneficiary_id,
        "quantity": line.quantity,
        "personalization_number": line.personalization_number,
        "personalization_name": line.personalization_name,
        "production_status": line.production_status,
        **payload.model_dump(exclude_unset=True),
    }
    form = OrderLineEditForm(post_data(values, single={"variant_id": "variant", "beneficiary_id": "beneficiary"}), instance=line, club=club)
    if not form.is_valid():
        raise form_error(form, aliases={"variant": "variant_id", "beneficiary": "beneficiary_id"})
    try:
        save_order_line(form.save(commit=False), old_variant_id=old_variant_id, user=request.user)
    except InventoryError as error:
        raise ApiError(409, "not_enough_stock", str(error)) from None
    return _detail(line.order)


@router.post(PREFIX + "/{uuid:order_id}/lines/{uuid:line_id}/mark-received", response={200: s.OrderDetailOut, **ERRORS}, summary="Mark one line received from production")
def line_mark_received(request, order_id: uuid.UUID, line_id: uuid.UUID):
    """Sets the line to received whatever its current status (the one-at-a-time route for personalized lines) and resyncs the order's production status."""
    club = _club(request)
    line = _line(club, order_id, line_id)
    mark_line_received(line)
    return _detail(line.order)


# --- payments ----------------------------------------------------------------------------------


def _payment(club, order_id, payment_id) -> Payment:
    return get_object_or_404(Payment.objects.filter(order__club=club, order__pk=order_id).select_related("order", "voucher"), pk=payment_id)


@router.post(PREFIX + "/{uuid:order_id}/payments", response={201: s.PaymentAddedOut, **ERRORS, **INVALID}, summary="Record a payment")
def add_payment(request, order_id: uuid.UUID, payload: s.AddPaymentIn):
    """Any amount (at least 0.01), partial or in full, including by voucher (`voucher_id`, usable and issued to the purchaser, their family or nobody).
    Honours `Idempotency-Key`."""
    club = _club(request)

    def work():
        order = _order(club, order_id)
        form = AddPaymentForm(post_data(payload.model_dump(), single={"voucher_id": "voucher"}), club=club, order=order)
        if not form.is_valid():
            raise form_error(form, aliases={"voucher": "voucher_id"})
        try:
            payment = record_payment(
                order,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data.get("reference", ""),
                voucher=form.cleaned_data.get("voucher"),
            )
        except PaymentError as error:
            raise _payment_error(error) from None
        payment = Payment.objects.select_related("voucher").get(pk=payment.pk)
        return 201, {"payment": _payment_out(payment), "order": _detail(order)}

    return idempotent(request, work)


@router.patch(PREFIX + "/{uuid:order_id}/payments/{uuid:payment_id}", response={200: s.OrderDetailOut, **ERRORS, **INVALID}, summary="Change a payment's method or reference")
def update_payment(request, order_id: uuid.UUID, payment_id: uuid.UUID, payload: s.PaymentUpdateIn):
    """Method and reference only; the amount, status and date stay (delete and add again). A non-voucher payment cannot become a voucher payment."""
    club = _club(request)
    payment = _payment(club, order_id, payment_id)
    values = {"method": payment.method, "reference": payment.reference, **payload.model_dump(exclude_unset=True)}
    form = PaymentEditForm(post_data(values), instance=payment)
    if not form.is_valid():
        raise form_error(form)
    form.save()
    return _detail(payment.order)


@router.delete(PREFIX + "/{uuid:order_id}/payments/{uuid:payment_id}", response={200: s.OrderDetailOut, **ERRORS}, summary="Delete a payment")
def delete_order_payment(request, order_id: uuid.UUID, payment_id: uuid.UUID):
    """A voucher payment gives its amount back to the voucher. The order's payment status is recomputed from the payments left
    (falling back to `pending` when none remain), unless the order is refunded. Fulfilment is untouched."""
    club = _club(request)
    payment = _payment(club, order_id, payment_id)
    order = payment.order
    remove_payment(payment)
    return _detail(order)


# --- invoice -----------------------------------------------------------------------------------


@router.get(PREFIX + "/{uuid:order_id}/invoice/pdf", response={200: None, **ERRORS, 503: ErrorOut}, summary="Order invoice as a PDF download")
def invoice_pdf(request, order_id: uuid.UUID):
    """`application/pdf` (attachment named after the invoice number); 404 when the order has no invoice, 503 `pdf_unavailable` without the PDF libraries."""
    club = _club(request)
    order = _order(club, order_id)
    invoice = get_object_or_404(Invoice, order=order, club=club)
    try:
        pdf = render_invoice_pdf(invoice)
    except ShopInvoicePDFError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    return _file(pdf, "application/pdf", f"{invoice.number}.pdf")


# --- production workflow -----------------------------------------------------------------------


@router.post(PREFIX + "/export-production", response={202: JobOut, **ERRORS, **INVALID}, summary="Export the manufacturer order list (job)")
def export_production(request, payload: s.ProductionIn):
    """Builds one spreadsheet (a sheet per product) of every not-yet-submitted line of the selected merchandise products and marks them
    in production, so a repeat does not resend them. Runs as a job; its `result` is `{token, filename, lines}`: fetch the file once, within
    5 minutes, with `GET /manage/shop/orders/export-production/{token}`. `422 nothing_to_export` (immediately, or as the job's error) when nothing is pending.
    Honours `Idempotency-Key`."""
    club = _club(request)
    products = _merchandise(club, payload.product_ids)
    if not pending_production_lines(products).exists():
        raise ApiError(422, "nothing_to_export", _("No pending items for the selected products."))
    ids = [str(pk) for pk in products.values_list("pk", flat=True)]
    return idempotent(request, lambda: (202, start_job(request, export_production_job, ids)))


@router.get(PREFIX + "/export-production/{token}", response={200: None, **ERRORS}, summary="Download an exported order list (once)")
def download_production(request, token: str):
    """The `.xlsx` the export job built, as an attachment. One-time: the token is spent by the download, expires after 5 minutes and only
    works for the club it was issued to (404 otherwise)."""
    club = _club(request)
    stashed = pop_production_export(club, token)
    if stashed is None:
        raise ApiError(404, "not_found", _("This download is no longer available."))
    filename, content = stashed
    return _file(content, XLSX, filename)


@router.post(PREFIX + "/reprint-production", response={200: None, **ERRORS, **INVALID}, summary="Download what is in production again")
def reprint_production(request, payload: s.ProductionIn):
    """A fresh `.xlsx` of the lines currently in production for the selected merchandise products, unchanged: nothing is marked. For when the
    original export is lost or a manufacturer needs it resent. `422 nothing_to_download` when nothing is in production."""
    club = _club(request)
    lines = list(in_production_lines(_merchandise(club, payload.product_ids)))
    if not lines:
        raise ApiError(422, "nothing_to_download", _("No items currently in production for the selected products."))
    response = HttpResponse(content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="order-in-production-list.xlsx"'
    build_production_export(lines).save(response)
    return response


def _receive_summary(club) -> list[dict]:
    return [
        {
            "product_id": entry["product"].pk,
            "product_name": entry["product"].name,
            "personalized_count": entry["personalized_count"],
            "variants": [{"variant_id": row["variant"].pk if row["variant"] else None, "variant_name": row["variant"].name if row["variant"] else "", "expected_quantity": row["expected_quantity"]} for row in entry["variants"]],
        }
        for entry in production_receiving_summary(club)
    ]


@router.get(PREFIX + "/receive-production", response={200: s.ReceiveSummaryOut, **ERRORS}, summary="What is still out with manufacturers")
def receive_summary(request):
    """One row per product and variant with non-personalized lines in production, and the quantity expected back. Personalized lines are only
    counted (`personalized_count`): receive those one at a time."""
    return {"items": _receive_summary(_club(request))}


@router.post(PREFIX + "/receive-production", response={200: s.ReceiveResultOut, **ERRORS, **INVALID}, summary="Enter what arrived from production")
def receive(request, payload: s.ReceiveIn):
    """Each receipt is the physical count for a product and variant listed by `GET`; it is allocated to that item's oldest in-production lines
    first (a line the count cannot fully cover is left, and so is everything after it). A count above what was expected is reported as `surplus`, a
    short one as `shortfall`. A product or variant that is not listed (nothing in production) is `422`. Honours `Idempotency-Key`."""
    club = _club(request)

    def work():
        wanted = {(entry["product"].pk, row["variant"].pk if row["variant"] else None): (entry["product"], row["variant"]) for entry in production_receiving_summary(club) for row in entry["variants"]}
        receipts, errors = {}, []
        for index, receipt in enumerate(payload.receipts):
            key = (receipt.product_id, receipt.variant_id)
            if key not in wanted:
                errors.append(str(_("Receipt %(number)d: nothing of this product and variant is in production.") % {"number": index + 1}))
            elif wanted[key] in receipts:
                errors.append(str(_("Receipt %(number)d: this product and variant is listed twice.") % {"number": index + 1}))
            else:
                receipts[wanted[key]] = receipt.quantity
        if errors:
            raise ApiError(422, "validation_error", fields={"receipts": errors})

        results = receive_production(club, receipts)
        body = [
            {
                "product_id": r.product.pk,
                "variant_id": r.variant.pk if r.variant else None,
                "expected_quantity": r.expected_quantity,
                "received_quantity": r.received_quantity,
                "shortfall": r.shortfall,
                "surplus": r.surplus,
                "has_discrepancy": r.has_discrepancy,
                "allocated_line_ids": [line.pk for line in r.allocated_lines],
                "remaining_line_ids": [line.pk for line in r.remaining_lines],
            }
            for r in results
        ]
        return 200, {"results": body, "allocated_lines": sum(len(r.allocated_lines) for r in results), "discrepancies": sum(1 for r in results if r.has_discrepancy)}

    return idempotent(request, work)
