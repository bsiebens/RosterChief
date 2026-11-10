"""Staff side of public registration. ``/registrations``: the billing review (unconfirmed registrations, edit lines and a manual discount,
confirm the invoice, cancel, resend emails); ``/registration/settings``. Everything needs ``members.manage_members`` (403 otherwise; no separate
finance permission exists in v2). Rules live in ``registration.services``."""

import uuid

from django.db.models import Q
from django.http import HttpResponse
from ninja import Query, Router
from ninja.errors import HttpError

from api.schemas_registrations import ConfirmIn, RegistrationDetail, RegistrationListOut, RegistrationSettingsIn, RegistrationSettingsOut, ReviewIn, SentOut
from members.services import access
from registration.models import RegistrationBatch
from registration.services import config, invoicing, notifications
from shop.services.invoices import PDFUnavailable

router = Router()
settings_router = Router()


def _gate(request):
    access.require_admin(request.user)


def _batch(batch_id) -> RegistrationBatch:
    batch = RegistrationBatch.objects.select_related("contact", "season").filter(pk=batch_id).first()
    if batch is None:
        raise HttpError(404, "Not found.")
    return batch


def _person(m) -> dict | None:
    return {"id": m.pk, "name": str(m), "email": m.email} if m is not None else None


def _item(b: RegistrationBatch) -> dict:
    return {
        "id": b.pk, "status": b.status, "contact": _person(b.contact), "season_name": b.season.name, "submitted_at": b.submitted_at, "people_count": b.details.count(),
        "total": invoicing.total(b), "invoice_number": b.invoice_number, "confirmed_at": b.confirmed_at,
    }  # fmt: skip


def _detail(b: RegistrationBatch) -> dict:
    lines = []
    for d in b.details.select_related("member", "membership", "requested_team", "requested_position"):
        m = d.membership
        lines.append(
            {
                "id": d.pk, "member": _person(d.member), "membership_id": m.pk, "entry_kind": d.entry_kind, "description": d.description,
                "requested_team_id": d.requested_team_id, "requested_team_name": d.requested_team.name if d.requested_team else None, "requested_position_name": d.requested_position.name if d.requested_position else None,
                "requested_jersey_number": d.requested_jersey_number, "base_price": d.base_price, "discount_amount": d.discount_amount, "price_override": d.price_override,
                "excluded": d.excluded, "price": d.price, "is_credit": d.is_credit, "credit_share": d.credit_share, "payable": d.payable, "fee_amount": m.fee_amount, "early_payment_deadline": m.early_payment_deadline, "early_payment_discount": m.early_payment_discount,
            }
        )  # fmt: skip
    credit_total, credit_applied = invoicing.credits(b)
    return {
        **_item(b), "season_id": b.season_id, "lines": lines, "subtotal": invoicing.subtotal(b), "credit_total": credit_total, "credit_applied": credit_applied, "manual_discount_amount": b.manual_discount_amount, "manual_discount_note": b.manual_discount_note,
        "invoice_due_date": b.invoice_due_date, "invoice_sent_at": b.invoice_sent_at, "confirmation_sent_at": b.confirmation_sent_at, "cancelled_at": b.cancelled_at, "status_path": f"/join/status/{b.status_token}",
    }  # fmt: skip


@router.get("", response=RegistrationListOut, summary="Registrations (admin)", operation_id="list_registrations")
def list_registrations(request, status: str = "awaiting", q: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """``status``: ``awaiting`` (billing review, the default), ``confirmed``, ``cancelled`` or ``all``. ``q`` searches the contact."""
    _gate(request)
    qs = RegistrationBatch.objects.select_related("contact", "season")
    if status == "awaiting":
        qs = qs.filter(confirmed_at__isnull=True, cancelled_at__isnull=True)
    elif status == "confirmed":
        qs = qs.filter(confirmed_at__isnull=False, cancelled_at__isnull=True)
    elif status == "cancelled":
        qs = qs.filter(cancelled_at__isnull=False)
    if q.strip():
        qs = qs.filter(Q(contact__first_name__icontains=q.strip()) | Q(contact__last_name__icontains=q.strip()) | Q(contact__email__icontains=q.strip()) | Q(invoice_number__icontains=q.strip()))
    awaiting = RegistrationBatch.objects.filter(confirmed_at__isnull=True, cancelled_at__isnull=True).count()
    return {"count": qs.count(), "awaiting_count": awaiting, "results": [_item(b) for b in qs[offset : offset + limit]]}


@router.get("/{batch_id}", response=RegistrationDetail, summary="One registration with its lines (admin)", operation_id="get_registration")
def get_registration(request, batch_id: uuid.UUID):
    _gate(request)
    return _detail(_batch(batch_id))


@router.patch("/{batch_id}", response=RegistrationDetail, summary="Edit the billing review (admin)", operation_id="review_registration")
def review(request, batch_id: uuid.UUID, payload: ReviewIn):
    """Only while awaiting: line price (or ``null`` for the computed one), exclude a line, manual discount + note. Each membership's fee follows.
    422 ``confirmed`` / ``cancelled`` afterwards."""
    _gate(request)
    data = payload.dict(exclude_unset=True)
    batch = invoicing.update_review(request.user, _batch(batch_id), lines=data.get("lines"), manual_discount_amount=data.get("manual_discount_amount"), manual_discount_note=data.get("manual_discount_note"))
    return _detail(_batch(batch.pk))


@router.post("/{batch_id}/confirm", response=RegistrationDetail, summary="Confirm the invoice (admin)", operation_id="confirm_registration")
def confirm(request, batch_id: uuid.UUID, payload: ConfirmIn):
    """Numbers the invoice (``REG-<year>-0001``), sets the due date, rosters the requested teams, emails the invoice (PDF) when ``send``. From now on
    the people show up in the Sign-ups queue."""
    _gate(request)
    batch = invoicing.confirm_invoice(request.user, _batch(batch_id), due_date=payload.due_date, send=payload.send)
    return _detail(_batch(batch.pk))


@router.post("/{batch_id}/cancel", response=RegistrationDetail, summary="Cancel the registration (admin)", operation_id="cancel_registration")
def cancel(request, batch_id: uuid.UUID):
    _gate(request)
    batch = invoicing.cancel_batch(request.user, _batch(batch_id))
    return _detail(_batch(batch.pk))


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


@settings_router.get("/settings", response=RegistrationSettingsOut, summary="Registration settings (admin)", operation_id="get_registration_settings")
def get_settings(request):
    _gate(request)
    s = config.get_settings()
    return {"is_open": s.is_open, "intro_text": s.intro_text, "terms_text": s.terms_text, "payment_instructions": s.payment_instructions, "invoice_due_days": s.invoice_due_days}


@settings_router.put("/settings", response=RegistrationSettingsOut, summary="Change the registration settings (admin)", operation_id="update_registration_settings")
def update_settings(request, payload: RegistrationSettingsIn):
    _gate(request)
    s = config.update_settings(request.user, **payload.dict(exclude_unset=True))
    return {"is_open": s.is_open, "intro_text": s.intro_text, "terms_text": s.terms_text, "payment_instructions": s.payment_instructions, "invoice_due_days": s.invoice_due_days}
