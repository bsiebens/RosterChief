"""Management API: season fees and dues invoices (API.md §17).

The JSON counterpart of the ``/manage/memberships/*`` screens and the invoice list
(management/views.py). The season-wide list itself is ``GET /manage/members/memberships``
(members router); this router adds everything else of the fee workflow:

* KPI summary, mark paid (bulk and per membership), record a payment;
* the dues invoice of a membership (JSON and PDF download);
* send invoices and send reminders: Celery jobs (``202`` + ``JobOut``, poll ``GET /manage/jobs/{id}``);
* the fee list as a PDF: a direct file download (``GET .../export``), built synchronously like the
  web screen -- a job's result is JSON, so there'd be nowhere to put the file;
* the club's invoice list (shop orders, dues, registrations).

Who may do what is the web's rule: every ``/manage/members/memberships/*`` screen is club-admin only
(``is_club_admin``); the invoice list is for a club admin, or a shop manager while the shop is on
(then without the dues and registration rows). Logic lives in the services the views call:
club.services.fees / invoicing / invoice_rows, management.pdf.
"""

import base64
import json
import uuid
from typing import Annotated, Literal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.auth import require_club_member
from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.jobs import start_job
from api.core.pagination import clamp_limit
from api.core.schemas import ErrorOut, JobOut
from api.panel.guards import panel_club, require
from api.panel.schemas import fees as s
from api.protected.services import club_features
from club.models import ClubMembership, DuesInvoice, Season
from club.services.access import can_manage_shop, current_season, is_club_admin
from club.services.fees import annotate_fee_rows, fee_kpis, mark_as_paid, mark_many_as_paid, record_payment, remaining_balance, season_memberships
from club.services.invoice_rows import KINDS, invoice_rows
from club.services.invoicing import DuesInvoicePDFError, invoice_pdf
from club.tasks import send_dues_invoices_job, send_invoice_reminders_job
from management.forms import RecordFeePaymentForm
from management.pdf import PDFExportError, membership_list_pdf

router = Router(tags=["panel: fees"])

PREFIX = "/manage/members/memberships"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
FILE = {503: ErrorOut}


# --- helpers -----------------------------------------------------------------------------------


def _admin_club(request):
    """The club on this host for a club admin, as every web fee screen (ClubAdminRequiredMixin)."""
    club = panel_club(request)
    require(request, club, is_club_admin)
    return club


def _membership(club, membership_id) -> ClubMembership:
    return get_object_or_404(ClubMembership.objects.select_related("member"), pk=membership_id, club=club)


def _fee_out(membership: ClubMembership) -> dict:
    return {
        "id": membership.pk,
        "member_id": membership.member_id,
        "first_name": membership.member.first_name,
        "last_name": membership.member.last_name,
        "status": membership.status,
        "fee_status": membership.fee_status,
        "fee_amount": membership.fee_amount,
        "amount_paid": membership.amount_paid,
        "remaining_balance": remaining_balance(membership),
    }


def _pdf_response(pdf: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- fee list: summary, export -----------------------------------------------------------------


@router.get(PREFIX + "/summary", response={200: s.FeeSummaryOut, **ERRORS}, summary="KPI summary of the fee list")
def fee_summary(request):
    """The numbers above the web fee list, for the current season. Club admins only."""
    club = _admin_club(request)
    season = current_season(club)
    return {"season_id": season.pk if season else None, **fee_kpis(club, season)}


@router.get(PREFIX + "/export", response={200: None, **ERRORS, **FILE}, summary="Fee list as a PDF download")
def export_fee_list(
    request,
    season_id: uuid.UUID | None = None,
    fee_status: Annotated[str | None, Query(description="Default `not_paid`; `all` or a single fee status. Same filters as the list.")] = "not_paid",
    status: str | None = "all",
    team_id: uuid.UUID | None = None,
    q: str | None = None,
):
    """`application/pdf` (attachment), built synchronously from the same filtered list and KPI figures as
    `GET /manage/members/memberships`. 503 `pdf_unavailable` when the server lacks the PDF libraries."""
    club = _admin_club(request)
    season = get_object_or_404(Season, pk=season_id, club=club) if season_id else current_season(club)
    memberships = annotate_fee_rows(club, season_memberships(club, season, fee_status=fee_status or "all", status=status or "all", team_id=team_id or "", q=q or ""))
    kpis = fee_kpis(club, current_season(club))
    context = {
        "club": club,
        "generated_at": timezone.now(),
        "selected_season": season,
        "memberships": memberships,
        "kpi_total": kpis["total"],
        "kpi_paid": kpis["paid"],
        "kpi_partial": kpis["partial"],
        "kpi_unpaid": kpis["unpaid"],
        "kpi_waived": kpis["waived"],
        "kpi_paid_rate": kpis["paid_rate"],
        "kpi_overdue_invoices": kpis["overdue_invoices"],
    }
    try:
        pdf = membership_list_pdf(context)
    except PDFExportError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    return _pdf_response(pdf, f"memberships-{season}.pdf" if season else "memberships.pdf")


# --- payments ----------------------------------------------------------------------------------


@router.post(PREFIX + "/mark-paid", response={200: s.MarkPaidOut, **ERRORS, **INVALID}, summary="Mark many memberships paid")
def mark_paid(request, payload: s.MembershipIdsIn):
    """Settles each membership (its remaining balance is recorded as a payment). Ids of other clubs are
    ignored; `updated` is how many were. Does not activate the membership (that is the sign-up approval). Club admins only."""
    club = _admin_club(request)
    return {"updated": mark_many_as_paid(club, [str(pk) for pk in payload.membership_ids], recorded_by=request.user)}


@router.post(PREFIX + "/{uuid:membership_id}/mark-fully-paid", response={200: s.MembershipFeeOut, **ERRORS}, summary="Mark one membership fully paid")
def mark_fully_paid(request, membership_id: uuid.UUID):
    club = _admin_club(request)
    membership = _membership(club, membership_id)
    mark_as_paid(membership, recorded_by=request.user)
    membership.refresh_from_db()
    return _fee_out(membership)


@router.post(PREFIX + "/{uuid:membership_id}/record-payment", response={201: s.RecordPaymentOut, **ERRORS, **INVALID}, summary="Record a payment against a membership")
def record_fee_payment(request, membership_id: uuid.UUID, payload: s.RecordPaymentIn):
    """Any amount (at least 0.01), partial or in full; `fee_status` follows. Honours `Idempotency-Key`. Club admins only."""
    club = _admin_club(request)
    membership = _membership(club, membership_id)
    form = RecordFeePaymentForm(post_data(payload.model_dump()))
    if not form.is_valid():
        raise form_error(form)

    def work():
        payment = record_payment(
            membership,
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            reference=form.cleaned_data["reference"],
            note=form.cleaned_data["note"],
            recorded_by=request.user,
        )
        membership.refresh_from_db()
        return 201, _record_body(payment, membership)

    return idempotent(request, work)


def _record_body(payment, membership) -> dict:
    return {
        "payment": {"id": payment.pk, "amount": payment.amount, "method": payment.method, "reference": payment.reference, "note": payment.note, "paid_at": payment.paid_at},
        "membership": _fee_out(membership),
    }


# --- dues invoices -----------------------------------------------------------------------------


def _dues_invoice(club, membership_id) -> DuesInvoice:
    return get_object_or_404(DuesInvoice.objects.select_related("membership__member"), membership__pk=membership_id, club=club)


@router.get(PREFIX + "/{uuid:membership_id}/invoice", response={200: s.DuesInvoiceOut, **ERRORS}, summary="Dues invoice of a membership")
def dues_invoice_detail(request, membership_id: uuid.UUID):
    """The invoice the member or guardian received (404 when none was created). Club admins only."""
    club = _admin_club(request)
    invoice = _dues_invoice(club, membership_id)
    return {
        "id": invoice.pk,
        "number": invoice.number,
        "amount": invoice.amount,
        "due_date": invoice.due_date,
        "sent_at": invoice.sent_at,
        "sent_to_email": invoice.sent_to_email,
        "sent_to_guardian": invoice.sent_to_guardian,
        "last_reminder_sent_at": invoice.last_reminder_sent_at,
        "reminder_count": invoice.reminder_count,
        "is_paid": invoice.is_paid,
        "is_overdue": invoice.is_overdue,
        "membership": _fee_out(invoice.membership),
    }


@router.get(PREFIX + "/{uuid:membership_id}/invoice/pdf", response={200: None, **ERRORS, **FILE}, summary="Dues invoice as a PDF download")
def dues_invoice_pdf(request, membership_id: uuid.UUID):
    """`application/pdf` (attachment named after the invoice number). 503 `pdf_unavailable` without the PDF libraries."""
    club = _admin_club(request)
    invoice = _dues_invoice(club, membership_id)
    try:
        pdf = invoice_pdf(invoice)
    except DuesInvoicePDFError as error:
        raise ApiError(503, "pdf_unavailable", str(error)) from None
    return _pdf_response(pdf, f"{invoice.number}.pdf")


@router.post(PREFIX + "/send-invoices", response={202: JobOut, **ERRORS, **INVALID}, summary="Send dues invoices (job)")
def send_invoices(request, payload: s.SendInvoicesIn):
    """One invoice per membership, mailed to the member or, without an email, a parent/guardian; all share
    `due_in_days`. Runs as a job; its `result` is `{sent, failed, unreachable}` (`failed`: recorded but not
    mailed; `unreachable`: nobody to send to, no invoice made). Ids of other clubs are ignored. Honours `Idempotency-Key`."""
    _admin_club(request)
    ids = [str(pk) for pk in dict.fromkeys(payload.membership_ids)]
    return idempotent(request, lambda: (202, start_job(request, send_dues_invoices_job, ids, payload.due_in_days)))


@router.post(PREFIX + "/send-invoice-reminders", response={202: JobOut, **ERRORS}, summary="Remind everyone past due (job)")
def send_invoice_reminders(request):
    """Every sent, unpaid invoice past its due date, club-wide (dues and registration invoices), regardless of any
    list filter. Runs as a job; its `result` is `{sent, failed}`. Honours `Idempotency-Key`."""
    _admin_club(request)
    return idempotent(request, lambda: (202, start_job(request, send_invoice_reminders_job)))


# --- invoice list ------------------------------------------------------------------------------


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], int) and value[0] >= 0:
            return value[0]
    except ValueError, TypeError:
        pass
    raise ApiError(400, "invalid_cursor", _("This page link is no longer valid. Start from the first page."))


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([offset]).encode()).decode().rstrip("=")


@router.get("/manage/invoices", response={200: s.InvoiceListOut, **ERRORS}, summary="Every invoice the club has sent")
def list_invoices(
    request,
    type: Annotated[Literal["all", "order", "dues", "registration"] | None, Query(description="Default `all`.")] = "all",
    q: Annotated[str | None, Query(description="Billed person's name, or anyone in their family.")] = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Shop order, membership dues and registration invoices as one list, newest first. A club admin sees
    all three kinds; a shop manager (shop feature on) only orders, whatever `type` asks. The kinds are
    merged in memory, so the cursor is an opaque offset (rows added meanwhile can shift a page)."""
    # Not panel_club: the web gate (InvoiceListView.test_func) lets a shop manager in without any other management role.
    club = require_club_member(request)
    admin = is_club_admin(request.user, club)
    if not admin:
        require(request, club, lambda user, c: club_features(c)["shop"] and can_manage_shop(user, c))
    rows = invoice_rows(club, kind=type or "all", search=q or "", include_financial=admin)
    start, size = _offset(cursor), clamp_limit(limit)
    page = rows[start : start + size]
    items = [
        {
            "kind": row["kind"],
            "number": row["number"],
            "billed_to_member_id": row["billed_to"].pk if row["billed_to"] else None,
            "billed_to_name": row.get("billed_to_name") or (str(row["billed_to"]) if row["billed_to"] else ""),
            "amount": row["amount"],
            "date": row["date"],
            "status": str(row["status_label"]),
            "status_css": row["status_css"],
            "ref_id": row["ref_id"],
        }
        for row in page
    ]
    return {"items": items, "total": len(rows), "next_cursor": _cursor(start + size) if start + size < len(rows) else None, "kinds": list(KINDS) if admin else ["order"]}
