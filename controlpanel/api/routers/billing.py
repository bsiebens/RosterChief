"""Billing: plan terms and prices, per-club billing, dues (periods), payments and invoices."""

import re
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django_countries import countries
from ninja import Query, Router, Schema
from ninja.pagination import paginate

from billing.models import BillingSettings, ClubBilling, Due, DuePayment, Invoice, PlanPrice
from billing.services import dues as dues_svc
from billing.services import invoices as invoices_svc
from club.models import Club
from controlpanel import audit
from features.models import Plan

from ..auth import superuser_auth
from ..errors import ApiError
from ..idempotency import idempotent
from ..schemas import (
    BillingPlanOut,
    BillingSettingsIn,
    ClubBillingOut,
    CycleOut,
    DueOut,
    InvoiceIssueIn,
    InvoiceOut,
    IssuerSettingsIO,
    OpenPeriodIn,
    PaymentIn,
    PlanRef,
    PriceIn,
    PriceOut,
    SubscribeBillingIn,
    TermsIO,
)

router = Router(tags=["billing"])


def _club(club_id: UUID) -> Club:
    return get_object_or_404(Club.objects.exclude(schema_name="public"), pk=club_id)


def _dues():
    return Due.objects.select_related("club", "plan", "invoice")


def _due(due_id: UUID) -> Due:
    return get_object_or_404(_dues(), pk=due_id)


def _plan(plan_id: UUID) -> Plan:
    return get_object_or_404(Plan.objects.select_related("terms"), pk=plan_id)


# --- plan terms and prices -------------------------------------------------------------------------------------------


@router.get("/billing/plans", response=list[BillingPlanOut], summary="Plans with their billing terms and dated prices")
def billing_plans(request):
    return [BillingPlanOut.of(p) for p in Plan.objects.select_related("terms").prefetch_related("prices").order_by("name")]


@router.put("/billing/plans/{plan_id}/terms", response=BillingPlanOut, summary="Set a plan's period length, invoice lead time and grace")
def put_terms(request, plan_id: UUID, payload: TermsIO):
    plan = _plan(plan_id)
    terms = plan.terms
    for field, value in payload.dict().items():
        setattr(terms, field, value)
    terms.full_clean()
    terms.save()
    audit.record(request.user, "billing.terms_updated", target=plan, data=payload.dict())
    return BillingPlanOut.of(_plan(plan_id))


@router.post("/billing/plans/{plan_id}/prices", response={201: PriceOut}, summary="Add a dated price (a rate change is always a new row)")
def add_price(request, plan_id: UUID, payload: PriceIn):
    plan = _plan(plan_id)
    if payload.amount < 0:
        raise ApiError(422, "validation_error", "The amount cannot be negative.", {"amount": ["Cannot be negative."]})
    if PlanPrice.objects.filter(plan=plan, active_from=payload.active_from).exists():
        raise ApiError(409, "conflict", "A price already exists for that date.")
    price = PlanPrice.objects.create(plan=plan, active_from=payload.active_from, amount=payload.amount)
    audit.record(request.user, "billing.price_added", target=plan, data={"active_from": str(payload.active_from), "amount": str(payload.amount)})
    return 201, PriceOut(id=price.pk, active_from=price.active_from, amount=price.amount)


# --- per club --------------------------------------------------------------------------------------------------------


def _club_billing(club: Club) -> ClubBillingOut:
    billing = ClubBilling.objects.filter(club=club).first()
    plan = dues_svc.current_plan(club)
    dues = list(_dues().filter(club=club).prefetch_related("payments"))
    owed = sum((d.balance for d in dues if d.is_owing), Decimal("0"))
    return ClubBillingOut(
        billed=billing is not None,
        plan=PlanRef(id=plan.pk, code=plan.code, name=plan.name) if plan else None,
        auto_renew=billing.auto_renew if billing else None,
        auto_archive=billing.auto_archive if billing else None,
        notes=billing.notes if billing else "",
        trial_ends_at=billing.trial_ends_at if billing else None,
        downgraded_at=billing.downgraded_at if billing else None,
        owed=owed,
        dues=[DueOut.of(d) for d in dues],
    )


@router.get("/clubs/{club_id}/billing", response=ClubBillingOut, summary="A club's plan, billing settings and all its periods")
def club_billing(request, club_id: UUID):
    return _club_billing(_club(club_id))


@router.patch("/clubs/{club_id}/billing", response=ClubBillingOut, summary="Change auto-renew, auto-archive or notes")
def patch_club_billing(request, club_id: UUID, payload: BillingSettingsIn):
    club = _club(club_id)
    billing = get_object_or_404(ClubBilling, club=club)
    changes = payload.dict(exclude_unset=True)
    for field, value in changes.items():
        setattr(billing, field, value)
    billing.save()
    audit.record(request.user, "billing.settings_updated", club=club, data=changes)
    return _club_billing(club)


@router.post("/clubs/{club_id}/billing/subscribe", response={201: ClubBillingOut}, summary="Bill a club on a plan (first time: opens the first period; trial_days starts with a free trial period)")
@idempotent
def subscribe(request, club_id: UUID, payload: SubscribeBillingIn):
    club = _club(club_id)
    plan = _plan(payload.plan_id)
    _, due = dues_svc.subscribe(club, plan, start=payload.start, auto_renew=payload.auto_renew, auto_archive=payload.auto_archive, notes=payload.notes, trial_days=payload.trial_days, actor=request.user)
    audit.record(request.user, "billing.subscribed", club=club, data={"plan": plan.code, "opened_period": due is not None, "trial_days": payload.trial_days})
    return 201, _club_billing(club)


@router.post("/clubs/{club_id}/billing/periods", response={201: DueOut}, summary="Open the next period (restores the club first if it is archived)")
@idempotent
def open_period(request, club_id: UUID, payload: OpenPeriodIn):
    club = _club(club_id)
    due = dues_svc.reactivate(club, start=payload.start, actor=request.user) if club.is_archived else dues_svc.open_period(club, start=payload.start, actor=request.user)
    audit.record(request.user, "billing.period_opened", club=club, target=due)
    return 201, DueOut.of(_due(due.pk), with_payments=True)


# --- dues, payments, invoices ----------------------------------------------------------------------------------------


class DueFilters(Schema):
    status: str = "owing"  # owing | unpaid | partial | paid | waived | cancelled | all
    state: str | None = None  # overdue | in_grace | issued_ahead | settled (same meaning as DueOut.state)
    club_id: UUID | None = None
    plan_id: UUID | None = None
    search: str | None = None  # club name / slug, or invoice number
    overdue: bool | None = None


STATES = ("overdue", "in_grace", "issued_ahead", "settled")


@router.get("/billing/dues", response=list[DueOut], summary="Periods across clubs (default: what is owed, soonest grace end first); paginated with limit/offset")
@paginate
def list_dues(request, filters: DueFilters = Query(...)):
    qs = _dues()
    if filters.status == "owing":
        qs = qs.filter(status__in=Due.OWING)
    elif filters.status != "all":
        if filters.status not in dict(Due.STATUS_CHOICES):
            raise ApiError(422, "validation_error", "Unknown status.", {"status": ["Use owing, all or a status."]})
        qs = qs.filter(status=filters.status)
    if filters.club_id:
        qs = qs.filter(club_id=filters.club_id)
    if filters.plan_id:
        qs = qs.filter(plan_id=filters.plan_id)
    if filters.search and filters.search.strip():
        term = filters.search.strip()
        qs = qs.filter(Q(club__name__icontains=term) | Q(club__legal_name__icontains=term) | Q(club__slug__icontains=term) | Q(invoice__number__icontains=term))
    if filters.state is not None:
        if filters.state not in STATES:
            raise ApiError(422, "validation_error", "Unknown state.", {"state": [f"Use one of: {', '.join(STATES)}."]})
        day = dues_svc.today()
        if filters.state == "overdue":
            qs = qs.filter(pk__in=dues_svc.dues_overdue(day).values("pk"))
        elif filters.state == "in_grace":
            qs = qs.filter(pk__in=dues_svc.dues_in_grace(day).values("pk"))
        elif filters.state == "issued_ahead":
            qs = qs.filter(status__in=Due.OWING, period_start__gt=day)
        else:
            qs = qs.exclude(status__in=Due.OWING)
    if filters.overdue is not None:
        overdue_ids = dues_svc.dues_overdue().values("pk")
        qs = qs.filter(pk__in=overdue_ids) if filters.overdue else qs.exclude(pk__in=overdue_ids)
    qs = qs.order_by("grace_until", "id") if filters.status == "owing" else qs.order_by("-period_start", "id")
    return [DueOut.of(d) for d in qs]


@router.get("/billing/summary", summary="Totals: owed, overdue, in grace, invoices waiting to be sent")
def summary(request):
    owed = dues_svc.owing_dues().aggregate(total=Sum("amount"), vat=Sum("vat_amount"), paid=Sum("amount_paid"))
    return {
        "owed": (owed["total"] or Decimal("0")) + (owed["vat"] or Decimal("0")) - (owed["paid"] or Decimal("0")),
        "overdue_count": dues_svc.dues_overdue().count(),
        "in_grace_count": dues_svc.dues_in_grace().count(),
        "invoices_to_send": sum(1 for inv in _unsent_invoices()),
        "clubs_unbilled": Club.objects.exclude(schema_name="public").filter(archived_at__isnull=True, billing__isnull=True).count(),
    }


def _unsent_invoices():
    from billing.models import Invoice

    return Invoice.objects.filter(sent_at__isnull=True).exclude(due__amount=0, due__vat_amount=0)


@router.get("/billing/dues/{due_id}", response=DueOut, summary="One period with its payments and invoice")
def get_due(request, due_id: UUID):
    return DueOut.of(_due(due_id), with_payments=True)


@router.post("/billing/dues/{due_id}/payments", response={201: DueOut}, summary="Record a payment")
@idempotent
def record_payment(request, due_id: UUID, payload: PaymentIn):
    due = _due(due_id)
    if payload.method not in dict(DuePayment.METHOD_CHOICES):
        raise ApiError(422, "validation_error", "Unknown method.", {"method": [f"Use one of: {', '.join(dict(DuePayment.METHOD_CHOICES))}."]})
    payment = dues_svc.record_payment(due, amount=payload.amount, method=payload.method, reference=payload.reference, paid_at=payload.paid_at, note=payload.note, user=request.user)
    audit.record(request.user, "billing.payment_recorded", club=due.club, target=due, data={"payment": str(payment.pk), "amount": str(payload.amount)})
    return 201, DueOut.of(_due(due_id), with_payments=True)


@router.delete("/billing/dues/{due_id}/payments/{payment_id}", response=DueOut, summary="Remove a payment (the period is re-settled)")
def remove_payment(request, due_id: UUID, payment_id: UUID):
    due = _due(due_id)
    payment = get_object_or_404(DuePayment, pk=payment_id, due=due)
    dues_svc.remove_payment(payment)
    audit.record(request.user, "billing.payment_removed", club=due.club, target=due, data={"amount": str(payment.amount)})
    return DueOut.of(_due(due_id), with_payments=True)


@router.post("/billing/dues/{due_id}/waive", response=DueOut, summary="Waive an unpaid period that has no payments")
def waive(request, due_id: UUID):
    due = _due(due_id)
    dues_svc.waive(due)
    audit.record(request.user, "billing.due_waived", club=due.club, target=due)
    return DueOut.of(_due(due_id), with_payments=True)


def _invoice(due_id: UUID):
    due = _due(due_id)
    return invoices_svc.issue_invoice(due)  # idempotent: creates it if the period somehow has none


def _number_error(exc: ValueError) -> ApiError:
    return ApiError(422, "validation_error", str(exc), {"number": [str(exc)]})


@router.get("/billing/dues/{due_id}/invoice", response=InvoiceOut, summary="Invoice data for a period")
def get_invoice(request, due_id: UUID):
    return InvoiceOut.of(_invoice(due_id))


@router.post("/billing/dues/{due_id}/invoice", response=InvoiceOut, auth=superuser_auth, summary="Issue the invoice now, optionally with a custom number (idempotent: returns the existing one)")
def issue_invoice(request, due_id: UUID, payload: InvoiceIssueIn | None = None):
    due = _due(due_id)
    existing = Invoice.objects.filter(due=due).first()
    if existing:
        return InvoiceOut.of(existing)
    try:
        invoice = invoices_svc.issue_invoice(due, number=payload.number if payload else None)
    except ValueError as exc:
        raise _number_error(exc) from None
    audit.record(request.user, "billing.invoice_issued", club=due.club, target=due, data={"number": invoice.number, "custom_number": invoice.custom_number})
    return InvoiceOut.of(invoice)


@router.get("/billing/dues/{due_id}/invoice.pdf", response={200: None}, summary="Download the invoice as a PDF")
def invoice_pdf(request, due_id: UUID):
    invoice = _invoice(due_id)
    pdf = invoices_svc.invoice_pdf(invoice)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
    return response


@router.post("/billing/dues/{due_id}/invoice/send", response=InvoiceOut, summary="Email the invoice PDF to the club's administrators")
def send_invoice(request, due_id: UUID):
    invoice = invoices_svc.send_invoice(_invoice(due_id), actor=request.user)
    audit.record(request.user, "billing.invoice_sent", club=invoice.due.club, target=invoice.due, data={"number": invoice.number})
    return InvoiceOut.of(invoice)


@router.post("/billing/dues/{due_id}/invoice/mark-sent", response=InvoiceOut, summary="Record that the invoice was sent outside the system")
def mark_invoice_sent(request, due_id: UUID):
    invoice = invoices_svc.mark_invoice_sent_manually(_invoice(due_id), actor=request.user)
    audit.record(request.user, "billing.invoice_marked_sent", club=invoice.due.club, target=invoice.due, data={"number": invoice.number})
    return InvoiceOut.of(invoice)


# --- upcoming renewals -----------------------------------------------------------------------------------------------


@router.get("/billing/cycles", response=list[CycleOut], summary="Upcoming renewals of auto-renewing clubs (and lapsed clubs without auto-renew), soonest invoice first")
def cycles(request):
    day = dues_svc.today()
    return [CycleOut.of(r, day) for r in dues_svc.upcoming_renewals(day)]


class UnbilledClubOut(Schema):
    id: UUID
    name: str
    slug: str
    plan_code: str | None = None  # the (free) plan the club is on, if any


@router.get("/billing/unbilled", response=list[UnbilledClubOut], summary="Active clubs that are not billed (they use the platform for free)")
def unbilled(request):
    clubs = Club.objects.exclude(schema_name="public").filter(archived_at__isnull=True, billing__isnull=True).order_by("name")
    return [UnbilledClubOut(id=c.pk, name=c.name, slug=c.slug, plan_code=plan.code if (plan := dues_svc.current_plan(c)) else None) for c in clubs]


# --- issuer settings -------------------------------------------------------------------------------------------------

IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
BIC_RE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


@router.get("/billing/settings", response=IssuerSettingsIO, summary="Issuer details and VAT rate printed on invoices")
def get_settings(request):
    return IssuerSettingsIO.of(BillingSettings.current())


@router.put("/billing/settings", response=IssuerSettingsIO, auth=superuser_auth, summary="Replace the issuer details and VAT rate (superuser). Only affects invoices issued afterwards")
def put_settings(request, payload: IssuerSettingsIO):
    data = {k: (v.strip() if isinstance(v, str) else v) for k, v in payload.dict().items()}
    errors: dict[str, list[str]] = {}
    data["iban"] = re.sub(r"\s+", "", data["iban"]).upper()
    data["bic"] = re.sub(r"\s+", "", data["bic"]).upper()
    data["country"] = data["country"].upper()
    if data["iban"] and not IBAN_RE.match(data["iban"]):
        errors["iban"] = ["Not a valid IBAN."]
    if data["bic"] and not BIC_RE.match(data["bic"]):
        errors["bic"] = ["Not a valid BIC."]
    if data["country"] and data["country"] not in dict(countries):
        errors["country"] = ["Unknown country code."]
    if data["contact_email"]:
        try:
            validate_email(data["contact_email"])
        except ValidationError:
            errors["contact_email"] = ["Not a valid email address."]
    if not 0 <= data["archive_after_days"] <= 365:
        errors["archive_after_days"] = ["Must be between 0 and 365."]
    if not Decimal("0") <= data["vat_rate"] <= Decimal("100"):
        errors["vat_rate"] = ["Must be between 0 and 100."]
    elif data["vat_rate"].as_tuple().exponent < -2:
        errors["vat_rate"] = ["At most two decimals."]
    for field, limit in (("legal_name", 255), ("address", 255), ("city", 255), ("zip_code", 32), ("vat_id", 32), ("website", 255)):
        if len(data[field]) > limit:
            errors[field] = [f"At most {limit} characters."]
    if errors:
        raise ApiError(422, "validation_error", "Some settings are not valid.", errors)
    row = BillingSettings.current()
    before = IssuerSettingsIO.of(row).dict()
    for field, value in data.items():
        setattr(row, field, value)
    row.save()
    after = IssuerSettingsIO.of(row).dict()
    audit.record(request.user, "billing.issuer_settings_updated", target=row, data={"changed": {k: [str(before[k]), str(after[k])] for k in after if before[k] != after[k]}})
    return IssuerSettingsIO.of(row)
