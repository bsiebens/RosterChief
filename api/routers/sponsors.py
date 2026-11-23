"""Club sponsorship management. The legacy public sponsors endpoint remains read-only."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ninja import Field, File, Form, Router, Schema, UploadedFile
from ninja.errors import HttpError

from api.schemas_shop_orders import Money, MoneyIn
from members.services import access
from sponsoring import billing, services
from sponsoring.models import Sponsor

router = Router()


class SponsorIn(Schema):
    name: str
    url: str = ""
    start_date: date
    end_date: date | None = None
    remove_logo: bool = False


class BillingOut(Schema):
    #: none (no agreement) | paid_up | open | overdue | suspended (inactive because of non-payment)
    status: str
    overdue_days: int
    next_due_date: date | None
    open_amount: Money


class SponsorOut(Schema):
    id: UUID
    name: str
    url: str
    logo_url: str | None
    start_date: date
    end_date: date | None
    status: str
    billing: BillingOut


class InstallmentIn(Schema):
    id: UUID | None = None
    due_date: date
    amount: MoneyIn = Field(gt=0)


class AgreementIn(Schema):
    contact_name: str = Field(max_length=150)
    contact_email: str = Field(max_length=254)
    description: str = Field("", max_length=150)
    lead_days: int = Field(30, ge=0, le=365)
    grace_days: int = Field(14, ge=0, le=365)
    warn_days: int = Field(3, ge=0, le=365)
    installments: list[InstallmentIn] = Field(min_length=1, max_length=billing.MAX_INSTALLMENTS)


class InstallmentOut(Schema):
    id: UUID
    due_date: date
    amount: Money
    #: scheduled (not invoiced yet) | open | partially_paid | paid | overdue
    status: str
    overdue_days: int
    amount_paid: Money
    amount_due: Money
    invoice_number: str | None
    #: The invoice in finance (``/finance/charges/{order_id}``), when there is a live one.
    order_id: UUID | None


class AgreementOut(Schema):
    sponsor_id: UUID
    contact_name: str
    contact_email: str
    description: str
    lead_days: int
    grace_days: int
    warn_days: int
    #: The manual override: not suspended while on (until ``keep_active_until`` when set).
    keep_active: bool
    keep_active_until: date | None
    suspended_at: datetime | None
    installments: list[InstallmentOut]
    billing: BillingOut


class KeepActiveIn(Schema):
    keep_active: bool
    until: date | None = None


def _require_club(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


def _item(sponsor, today):
    status = "upcoming" if sponsor.start_date > today else "ended" if sponsor.end_date and sponsor.end_date < today else "suspended" if sponsor.suspended_at else "active"
    return {
        "id": sponsor.pk, "name": sponsor.name, "url": sponsor.url, "logo_url": sponsor.logo.url if sponsor.logo else None,
        "start_date": sponsor.start_date, "end_date": sponsor.end_date, "status": status, "billing": billing.sponsor_status(sponsor, today),
    }


@router.get("", response=list[SponsorOut], summary="List the club's sponsors")
def list_sponsors(request):
    _require_club(request)
    access.require_sponsors(request.user)
    today = timezone.localdate()
    return [_item(sponsor, today) for sponsor in Sponsor.objects.select_related("agreement")]


@router.get("/{sponsor_id}", response=SponsorOut, summary="View one sponsor")
def get_sponsor(request, sponsor_id: UUID):
    _require_club(request)
    access.require_sponsors(request.user)
    return _item(get_object_or_404(Sponsor, pk=sponsor_id), timezone.localdate())


def _save(request, payload, logo, sponsor_id=None):
    _require_club(request)
    values = {"name": payload.name.strip(), "url": payload.url.strip(), "start_date": payload.start_date, "end_date": payload.end_date}
    sponsor = services.save_sponsor(request.user, values, sponsor_id=sponsor_id, logo=logo, remove_logo=payload.remove_logo)
    return _item(sponsor, timezone.localdate())


@router.post("", response={201: SponsorOut}, summary="Add a sponsor and optionally upload its logo")
def create_sponsor(request, payload: Form[SponsorIn], logo: File[UploadedFile | None] = None):
    return 201, _save(request, payload, logo)


@router.post("/{sponsor_id}", response=SponsorOut, summary="Edit a sponsor and optionally replace or clear its logo")
def update_sponsor(request, sponsor_id: UUID, payload: Form[SponsorIn], logo: File[UploadedFile | None] = None):
    return _save(request, payload, logo, sponsor_id)


@router.delete("/{sponsor_id}", response={204: None}, summary="Delete a sponsor")
def delete_sponsor(request, sponsor_id: UUID):
    _require_club(request)
    services.delete_sponsor(request.user, sponsor_id)
    return 204, None


# ------------------------------------------------------------------------------------------------- the agreement and its installments


def _agreement_out(sponsor) -> dict | None:
    today = timezone.localdate()
    sponsor = Sponsor.objects.select_related("agreement").get(pk=sponsor.pk)
    agreement = getattr(sponsor, "agreement", None)
    if agreement is None:
        return None
    rows = []
    for installment in agreement.installments.select_related("order__invoice"):
        state = billing.status_of(installment, today)
        order = installment.order if billing._alive(installment.order) else None
        invoice = getattr(order, "invoice", None) if order else None
        rows.append({
            "id": installment.pk, "due_date": installment.due_date, "amount": installment.amount, "status": state["status"], "overdue_days": state["overdue_days"],
            "amount_paid": state["paid"], "amount_due": state["due"], "invoice_number": invoice.number if invoice else None, "order_id": order.pk if order else None,
        })  # fmt: skip
    return {
        "sponsor_id": sponsor.pk, "contact_name": agreement.contact_name, "contact_email": agreement.contact_email, "description": agreement.description,
        "lead_days": agreement.lead_days, "grace_days": agreement.grace_days, "warn_days": agreement.warn_days,
        "keep_active": agreement.keep_active, "keep_active_until": agreement.keep_active_until, "suspended_at": sponsor.suspended_at, "installments": rows, "billing": billing.sponsor_status(sponsor, today),
    }  # fmt: skip


@router.get("/{sponsor_id}/agreement", response=AgreementOut | None, summary="The sponsor's payment agreement and installments (none: null)", operation_id="get_sponsor_agreement")
def get_agreement(request, sponsor_id: UUID):
    _require_club(request)
    access.require_sponsors(request.user)
    return _agreement_out(get_object_or_404(Sponsor, pk=sponsor_id))


@router.put("/{sponsor_id}/agreement", response=AgreementOut, summary="Create or replace the agreement and its installment schedule", operation_id="save_sponsor_agreement")
def save_agreement(request, sponsor_id: UUID, payload: AgreementIn):
    _require_club(request)
    access.require_sponsors(request.user)
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id)
    values = payload.dict(exclude={"installments"})
    billing.save_agreement(sponsor, values, [{"id": row.id, "due_date": row.due_date, "amount": Decimal(row.amount)} for row in payload.installments])
    return _agreement_out(sponsor)


@router.put("/{sponsor_id}/agreement/keep-active", response=AgreementOut, summary="Keep the sponsor active even when installments are unpaid (optionally until a date)", operation_id="set_sponsor_keep_active")
def set_keep_active(request, sponsor_id: UUID, payload: KeepActiveIn):
    _require_club(request)
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id)
    billing.set_keep_active(request.user, sponsor, payload.keep_active, payload.until)
    return _agreement_out(sponsor)


@router.post("/{sponsor_id}/installments/{installment_id}/invoice", response=AgreementOut, summary="Send the installment's invoice (again when it is already out)", operation_id="send_sponsor_installment")
def send_installment(request, sponsor_id: UUID, installment_id: UUID):
    """No invoice (or a cancelled one): make and send it now. A live open one: send it again as a reminder. A paid one: 409."""
    _require_club(request)
    access.require_sponsors(request.user)
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id)
    installment = get_object_or_404(billing.SponsorInstallment.objects.select_related("order"), pk=installment_id, agreement__sponsor=sponsor)
    if billing.status_of(installment)["status"] == "paid":
        raise HttpError(409, "This installment is paid.")
    if billing.invoice_installment(installment, request.user) is None:
        billing.remind(installment)
    billing.evaluate(sponsor)
    return _agreement_out(sponsor)
