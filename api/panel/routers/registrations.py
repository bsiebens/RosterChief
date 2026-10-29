"""Management API: registrations review, confirmation and payment (API.md §17).

The JSON counterpart of the ``/manage/registrations/*`` screens (management/views.py):

* ``GET /manage/registrations``: the review queue, every registration whose invoice is not confirmed yet;
* ``GET /manage/registrations/{id}``: one registration, pending (editable lines plus the options a line
  can change to) or confirmed (the billed lines, totals and payment status);
* ``POST .../confirm``: the review in one action -- edit or exclude lines, extra discount, then number,
  email and place (a ``pending`` registration only);
* ``POST .../cancel``: withdraw a pending registration (cancels every person in it);
* ``POST .../mark-paid``: mark everyone in a confirmed registration paid;
* ``GET .../invoice/pdf``: the invoice document, the same one the family gets.

Confirmed registrations are listed with the club's invoices (``GET /manage/invoices?type=registration``,
``ref_id`` is the registration id here).

Who may do what is the web's rule: every one of these screens is club-admin only (``is_club_admin``).
Logic lives in registration.services (invoicing, notifications, review); validation of the lines is
management.forms.RegistrationInvoiceLineFormSet, fed with what a browser would have posted.
"""

import uuid

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import registrations as s
from club.models import ClubMembership
from club.services.access import is_club_admin
from club.services.invoice_rows import registration_invoice_status, registration_payment_code
from management.forms import RegistrationInvoiceConfirmForm, RegistrationInvoiceLineFormSet
from registration.models import RegistrationBatch
from registration.services.invoicing import RegistrationInvoicePDFError, active_batch_entries, batch_invoice_pdf, batch_totals, registrations_awaiting_confirmation
from registration.services.pricing import available_registration_products, team_number_pools, variant_prices
from registration.services.review import batch_memberships, cancel_batch, confirm_review, mark_batch_paid, price_snapshot, review_entries
from shop.models import ProductVariant

router = Router(tags=["panel: registrations"])

PREFIX = "/manage/registrations"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
FILE = {503: ErrorOut}

#: Form field -> JSON key of a review line.
LINE_ALIASES = {"product_variant": "product_variant_id", "requested_team": "requested_team_id", "requested_position": "requested_position_id"}


# --- helpers -----------------------------------------------------------------------------------


def _admin_club(request):
    """The club on this host for a club admin, as every web registrations screen (ClubAdminRequiredMixin)."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _batch(club, batch_id, *, confirmed: bool | None = None) -> RegistrationBatch:
    """The club's batch; ``confirmed`` True/False narrows to a confirmed/pending one (404 otherwise, as the web)."""
    batches = RegistrationBatch.objects.filter(club=club).select_related("season", "contact_member")
    if confirmed is True:
        batches = batches.filter(invoice_sent_at__isnull=False)
    elif confirmed is False:
        batches = batches.filter(invoice_sent_at__isnull=True)
    return get_object_or_404(batches, pk=batch_id)


def _line_out(entry) -> dict:
    member = entry.membership.member
    variant = entry.product_variant
    return {
        "id": entry.pk,
        "entry_kind": entry.entry_kind,
        "membership_id": entry.membership_id,
        "member_id": member.pk,
        "member_name": str(member),
        "membership_status": entry.membership.status,
        "fee_status": entry.membership.fee_status,
        "product_variant_id": entry.product_variant_id,
        "product_name": variant.product.name if variant else "",
        "variant_name": variant.name if variant else "",
        "price": entry.price,
        "discount_amount": entry.discount_amount,
        "excluded_from_invoice": entry.excluded_from_invoice,
        "requested_team_id": entry.requested_team_id,
        "requested_team_name": str(entry.requested_team) if entry.requested_team else "",
        "requested_jersey_number": entry.requested_jersey_number,
        "requested_position_id": entry.requested_position_id,
        "requested_position_name": str(entry.requested_position) if entry.requested_position else "",
    }


def _options(club, batch) -> dict:
    variants = ProductVariant.objects.filter(product__in=available_registration_products(club, season=batch.season), is_active=True).select_related("product")
    prices = variant_prices(club, batch.season)
    return {
        "variants": [{"id": variant.pk, "product_name": variant.product.name, "name": variant.name, "price": prices[str(variant.pk)]} for variant in variants if str(variant.pk) in prices],
        "team_number_pools": team_number_pools(club, batch.season),
    }


def _contact(batch) -> dict:
    return {"first_name": batch.contact_first_name, "last_name": batch.contact_last_name, "email": batch.contact_email, "phone": batch.contact_phone, "member_id": batch.contact_member_id}


def _detail(club, batch) -> dict:
    confirmed = batch.invoice_sent_at is not None
    billable = active_batch_entries(batch)
    subtotal, discount_amount, total = batch_totals(billable, batch.manual_discount_amount)
    memberships = batch_memberships(billable)
    paid = {ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED}
    return {
        "id": batch.pk,
        "status": "confirmed" if confirmed else "pending",
        "created": batch.created,
        "season_id": batch.season_id,
        "season_name": str(batch.season),
        "contact": _contact(batch),
        "invoice_number": batch.invoice_number,
        "invoice_sent_at": batch.invoice_sent_at,
        "invoice_due_date": batch.invoice_due_date,
        "manual_discount_amount": batch.manual_discount_amount,
        "manual_discount_note": batch.manual_discount_note,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "total": total,
        "payment_status": str(registration_invoice_status(memberships.values())[0]) if confirmed else None,
        "payment_status_code": registration_payment_code(memberships.values()) if confirmed else None,
        "all_paid": all(membership.fee_status in paid for membership in memberships.values()),
        "lines": [_line_out(entry) for entry in (billable if confirmed else review_entries(batch))],
        "options": None if confirmed else _options(club, batch),
    }


# --- queue and detail --------------------------------------------------------------------------


@router.get(PREFIX, response={200: s.RegistrationQueueOut, **ERRORS}, summary="Registrations awaiting review")
def registration_queue(request, q: str | None = Query(None, description="Name of anyone in it, or of their family.")):
    """Every registration whose invoice is not confirmed yet and that still has a billable line, newest first
    (the web queue). Not paginated: it is the work list. Confirmed ones are under `GET /manage/invoices`. Club admins only."""
    club = _admin_club(request)
    rows = registrations_awaiting_confirmation(club, (q or "").strip())
    items = [
        {
            "id": row["batch"].pk,
            "created": row["batch"].created,
            "season_id": row["batch"].season_id,
            "contact_name": f"{row['batch'].contact_first_name} {row['batch'].contact_last_name}".strip(),
            "contact_email": row["batch"].contact_email,
            "billed_to_member_id": row["batch"].contact_member_id,
            "total": row["total"],
            "lines": [_line_out(entry) for entry in row["entries"]],
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@router.get(PREFIX + "/{uuid:batch_id}", response={200: s.RegistrationOut, **ERRORS}, summary="One registration")
def registration_detail(request, batch_id: uuid.UUID):
    """Pending: the review screen's data (every line, editable, plus `options`). Confirmed: the checkout view
    (billed lines, totals, `payment_status`). Club admins only."""
    club = _admin_club(request)
    return _detail(club, _batch(club, batch_id))


# --- review and confirm ------------------------------------------------------------------------


def _line_post_data(entries: dict, payload: s.ConfirmIn) -> dict:
    """The review's lines as the browser would post them: each sent field over the line's current value."""
    lines = payload.lines
    data = {
        "lines-TOTAL_FORMS": str(len(lines)),
        "lines-INITIAL_FORMS": str(len(lines)),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, line in enumerate(lines):
        entry = entries[line.id]
        sent = line.model_fields_set
        price = line.price if "price" in sent and line.price is not None else entry.price
        excluded = line.excluded_from_invoice if "excluded_from_invoice" in sent and line.excluded_from_invoice is not None else entry.excluded_from_invoice
        values = {
            "id": entry.pk,
            "price": price,
            "product_variant": line.product_variant_id if "product_variant_id" in sent else entry.product_variant_id,
            "requested_team": line.requested_team_id if "requested_team_id" in sent else entry.requested_team_id,
            "requested_jersey_number": line.requested_jersey_number if "requested_jersey_number" in sent else entry.requested_jersey_number,
            "requested_position": line.requested_position_id if "requested_position_id" in sent else entry.requested_position_id,
        }
        for key, value in values.items():
            data[f"lines-{index}-{key}"] = "" if value is None else str(value)
        if excluded:
            data[f"lines-{index}-excluded_from_invoice"] = "on"
    return data


def _formset_error(formset) -> ApiError:
    fields = {}
    for index, errors in enumerate(formset.errors):
        for name, messages in errors.items():
            key = "non_field_errors" if name == "__all__" else LINE_ALIASES.get(name, name)
            fields[f"lines.{index}.{key}"] = [str(message) for message in messages]
    if formset.non_form_errors():
        fields["lines"] = [str(message) for message in formset.non_form_errors()]
    return ApiError(422, "validation_error", fields=fields)


@router.post(PREFIX + "/{uuid:batch_id}/confirm", response={200: s.ConfirmedOut, **ERRORS, **INVALID}, summary="Review and confirm a registration's invoice")
def confirm_registration(request, batch_id: uuid.UUID, payload: s.ConfirmIn):
    """The web review in one call: applies the `lines` changes (omitted lines and omitted fields stay), the extra
    discount and `due_in_days`, numbers and emails the invoice, and places every active line that has a team (players on
    the team, volunteers as staff). 404 once confirmed. 422 `nothing_to_invoice` when every line is cancelled or excluded
    (the line edits are kept, nothing is confirmed). `email_sent` false means the invoice is confirmed but the mail failed.
    Honours `Idempotency-Key`. Club admins only."""
    club = _admin_club(request)

    def work():
        batch = _batch(club, batch_id, confirmed=False)
        queryset = review_entries(batch)
        entries = {entry.pk: entry for entry in queryset}
        ids = [line.id for line in payload.lines]
        unknown = [pk for pk in ids if pk not in entries]
        if unknown or len(set(ids)) != len(ids):
            raise ApiError(422, "validation_error", fields={"lines": [str(_("Every line must belong to this registration and be listed once."))]})

        confirm_form = RegistrationInvoiceConfirmForm({"manual_discount_amount": str(payload.manual_discount_amount), "manual_discount_note": payload.manual_discount_note, "due_in_days": str(payload.due_in_days)})
        before_by_entry_id = price_snapshot(entries.values())
        formset = RegistrationInvoiceLineFormSet(_line_post_data(entries, payload), queryset=queryset, prefix="lines", form_kwargs={"club": club, "season": batch.season, "team_number_pools": team_number_pools(club, batch.season)})
        if not formset.is_valid():
            raise _formset_error(formset)
        if not confirm_form.is_valid():
            raise form_error(confirm_form)

        sent = confirm_review(
            batch,
            formset,
            before_by_entry_id=before_by_entry_id,
            manual_discount_amount=confirm_form.cleaned_data["manual_discount_amount"],
            manual_discount_note=confirm_form.cleaned_data["manual_discount_note"],
            due_in_days=confirm_form.cleaned_data["due_in_days"],
            request=request,
        )
        if sent is None:
            raise ApiError(422, "nothing_to_invoice", _("Every entry in this registration is now cancelled or excluded -- there is nothing left to confirm."))
        batch.refresh_from_db()
        return 200, {"email_sent": sent, "registration": _detail(club, batch)}

    return idempotent(request, work)


@router.post(PREFIX + "/{uuid:batch_id}/cancel", response={200: s.CancelledOut, **ERRORS}, summary="Cancel a pending registration")
def cancel_registration(request, batch_id: uuid.UUID):
    """Cancels every person's registration in it (a confirmed one is cancelled per person from the sign-up queue; 404 here).
    Club admins only."""
    club = _admin_club(request)
    batch = _batch(club, batch_id, confirmed=False)
    return {"cancelled": cancel_batch(batch)}


@router.post(PREFIX + "/{uuid:batch_id}/mark-paid", response={200: s.RegistrationOut, **ERRORS}, summary="Mark a confirmed registration paid")
def mark_registration_paid(request, batch_id: uuid.UUID):
    """Settles every person still in it (their remaining balance is recorded as a payment); 404 while pending. Club admins only."""
    club = _admin_club(request)
    batch = _batch(club, batch_id, confirmed=True)
    mark_batch_paid(batch, recorded_by=request.user)
    return _detail(club, batch)


@router.get(PREFIX + "/{uuid:batch_id}/invoice/pdf", response={200: None, **ERRORS, **FILE}, summary="Registration invoice as a PDF download")
def registration_invoice_pdf(request, batch_id: uuid.UUID):
    """`application/pdf` (attachment), the document the family gets. Any registration of the club, pending or not.
    503 `pdf_unavailable` without the PDF libraries. Club admins only."""
    club = _admin_club(request)
    batch = _batch(club, batch_id)
    try:
        pdf = batch_invoice_pdf(batch)
    except RegistrationInvoicePDFError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{club.slug}-registration-{batch.pk}.pdf"'
    return response
