"""Payments & dues (API.md §7.2), club host. Read-only: there's no online payment, so the
app shows what's owed, how to pay (the club's own instructions) and the invoice PDF."""

import uuid

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router

from api.core.errors import ApiError
from api.core.scope import club_scope
from mobile.services.payments import payments_overview
from registration.models import RegistrationBatch
from registration.services.invoicing import RegistrationInvoicePDFError, batch_invoice_pdf

from ..presenters import money
from ..schemas import ErrorOut, PaymentsOut

router = Router(tags=["payments"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _lines(row) -> list[dict]:
    lines = []
    for entry in row["entries"]:
        if entry.product_variant_id:
            label = entry.product_variant.full_name
        else:
            label = str(entry.get_entry_kind_display())
        lines.append({"label": label, "team": str(entry.requested_team) if entry.requested_team_id else None, "price": money(entry.price), "discount": money(entry.discount_amount) if entry.discount_amount > 0 else None})
    return lines


def _member(row) -> dict:
    membership = row["membership"]
    return {"member_id": membership.member_id, "membership_id": membership.pk, "balance": money(row["balance"]), "lines": _lines(row)}


def _early(offer) -> dict | None:
    if not offer:
        return None
    return {"deadline": offer["deadline"], "discount": money(offer["discount"]), "total": money(offer["discounted_total"])}


@router.get("/payments", response={200: PaymentsOut, **ERRORS}, summary="Payments & dues")
def payments(request):
    """Open balances of every managed person. People covered by one registration share one
    item (one invoice, one early-payment offer)."""
    scope = club_scope(request)
    items = []
    for display in payments_overview(scope.club, scope.people):
        if display["kind"] == "registration":
            group = display["group"]
            batch = group["batch"]
            items.append(
                {
                    "kind": "registration",
                    "registration_id": batch.pk,
                    "due_date": batch.invoice_due_date,
                    "total_balance": money(group["total_balance"]),
                    "members": [_member(row) for row in group["rows"]],
                    "manual_discount": money(batch.manual_discount_amount) if batch.manual_discount_amount > 0 else None,
                    "manual_discount_note": batch.manual_discount_note,
                    "early_payment": _early(group["early_payment"]),
                }
            )
        else:
            row = display["row"]
            invoice = row["invoice"]
            items.append(
                {
                    "kind": "membership",
                    "registration_id": None,
                    "due_date": invoice.due_date if invoice else None,
                    "total_balance": money(row["balance"]),
                    "members": [_member(row)],
                    "manual_discount": None,
                    "manual_discount_note": "",
                    "early_payment": _early(row["early_payment"]),
                }
            )
    return {"payment_instructions": scope.club.payment_instructions, "items": items}


@router.get(
    "/payments/registrations/{registration_id}/invoice.pdf",
    response={200: None, **ERRORS, 409: ErrorOut},
    summary="Registration invoice (PDF)",
    openapi_extra={"responses": {200: {"description": "The invoice.", "content": {"application/pdf": {}}}}},
)
def registration_invoice(request, registration_id: uuid.UUID):
    """Only for a registration covering a managed person, once the club has confirmed it."""
    scope = club_scope(request)
    batch = get_object_or_404(RegistrationBatch.objects.filter(club=scope.club, entries__membership__member__in=scope.people).distinct(), pk=registration_id)
    if batch.invoice_sent_at is None:
        raise ApiError(404, "not_found")
    try:
        pdf = batch_invoice_pdf(batch)
    except RegistrationInvoicePDFError as error:
        raise ApiError(409, "pdf_unavailable", str(error)) from error
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{scope.club.slug}-registration-{batch.pk}.pdf"'
    return response
