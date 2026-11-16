"""Finance beyond registrations. ``/finance/open-members``: memberships with a fee still open that did not come from a registration (a member
import, someone added by hand, an older sign-up), and ``/finance/memberships/{id}/payments`` to record a payment (also with a shop voucher) on one.
Everything needs ``registration.manage_finance``. Registration invoices are on ``/registrations``."""

import datetime
import uuid
from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.schemas_registrations import PersonRef
from members.models import ClubMembership, FeePayment
from members.services import access
from members.services import memberships as fees
from members.services.errors import Invalid
from registration.models import RegistrationBatch
from registration.services import installments
from shop.services import orders as shop_orders

router = Router()
Status = ClubMembership.StatusChoices
FeeStatus = ClubMembership.FeeStatus


class OpenMemberOut(Schema):
    membership_id: uuid.UUID
    member: PersonRef
    season_name: str
    status: str
    fee_status: str
    fee_amount: Decimal
    amount_paid: Decimal
    balance: Decimal
    early_payment_deadline: datetime.date | None = None


class OpenMembersOut(Schema):
    count: int
    results: list[OpenMemberOut]
    #: The club has the shop, so a payment can be made with a shop voucher.
    vouchers_enabled: bool = False


class MemberPaymentIn(Schema):
    amount: Decimal
    method: str = "bank_transfer"
    reference: str = ""
    paid_at: datetime.datetime | None = None
    note: str = ""
    voucher_number: str | None = None


class FinanceSummaryOut(Schema):
    """Amounts are decimal strings. ``overdue`` is what is open on invoices past their due date (``overdue_count`` invoices)."""

    invoiced: Decimal
    received: Decimal
    open: Decimal
    overdue: Decimal
    overdue_count: int


def _gate(request):
    access.require_finance(request.user)


def _open():
    """Memberships with a fee to pay that are not part of a registration (those are on the registrations list)."""
    return ClubMembership.objects.filter(fee_status__in=[FeeStatus.UNPAID, FeeStatus.PARTIALLY_PAID], fee_amount__gt=0, registration_details__isnull=True).exclude(status=Status.CANCELLED).select_related("member", "season")


def _row(m: ClubMembership) -> dict:
    return {
        "membership_id": m.pk, "member": {"id": m.member_id, "name": str(m.member), "email": m.member.email}, "season_name": m.season.name, "status": m.status, "fee_status": m.fee_status,
        "fee_amount": m.fee_amount, "amount_paid": m.amount_paid, "balance": fees.remaining_balance(m), "early_payment_deadline": m.early_payment_deadline,
    }  # fmt: skip


@router.get("/open-members", response=OpenMembersOut, summary="Members with an open fee outside registrations (finance)", operation_id="finance_open_members")
def open_members(request, q: str = "", limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0)):
    _gate(request)
    qs = _open()
    if q.strip():
        qs = qs.filter(Q(member__first_name__icontains=q.strip()) | Q(member__last_name__icontains=q.strip()) | Q(member__email__icontains=q.strip()))
    qs = qs.order_by("member__last_name", "member__first_name", "-season__start_date")
    return {"count": qs.count(), "results": [_row(m) for m in qs[offset : offset + limit]], "vouchers_enabled": request.features.has("shop")}


@router.post("/memberships/{membership_id}/payments", response={201: OpenMemberOut}, summary="Record a payment on a member's fee (finance)", operation_id="finance_record_member_payment")
def record_member_payment(request, membership_id: uuid.UUID, payload: MemberPaymentIn):
    """Full or partial; ``method=voucher`` takes the amount from the shop voucher ``voucher_number``. 422 for a waived fee or more than is open."""
    _gate(request)
    m = ClubMembership.objects.select_related("member", "season").filter(pk=membership_id).first()
    if m is None:
        raise HttpError(404, "Not found.")
    if payload.method == FeePayment.Method.VOUCHER and not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    voucher = shop_orders.find_voucher(payload.voucher_number) if payload.voucher_number else None
    on = timezone.localtime(payload.paid_at or timezone.now()).date()
    if fees.money(payload.amount) > fees.remaining_balance(m, today=on):
        raise Invalid(f"That is more than the {fees.remaining_balance(m, today=on)} still open.", {"amount": ["More than the open balance."]})
    fees.record_payment(request.user, m, payload.amount, method=payload.method, reference=payload.reference, paid_at=payload.paid_at, note=payload.note, voucher=voucher)
    m.refresh_from_db()
    return 201, _row(m)


ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)


@router.get("/summary", response=FinanceSummaryOut, summary="Totals of the confirmed invoices (finance)", operation_id="finance_summary")
def summary(
    request,
    season_id: uuid.UUID | None = None,
    from_: datetime.date | None = Query(None, alias="from", description="Invoices confirmed on or after this day."),
    to: datetime.date | None = Query(None, description="Invoices confirmed on or before this day."),
    q: str = "",
):
    """What the confirmed (not cancelled) registration invoices amount to: ``invoiced`` (the fees, less an early-payment discount that is still in
    force; waived fees count nothing), ``received`` (payments), ``open`` (still to pay) and ``overdue`` (open on invoices past their due date, or with
    a late instalment, and how many). Narrowed like the registrations list: ``q`` searches the contact and invoice number; ``season_id`` and the
    ``from`` / ``to`` days (of confirmation) narrow further. Sums are computed in the database: the early-payment discount is judged on today's date
    (a discount earned by paying in time before the deadline is not looked up per payment)."""
    _gate(request)
    today = timezone.localdate()
    batches = RegistrationBatch.objects.filter(confirmed_at__isnull=False, cancelled_at__isnull=True)
    if season_id:
        batches = batches.filter(season_id=season_id)
    if from_:
        batches = batches.filter(confirmed_at__date__gte=from_)
    if to:
        batches = batches.filter(confirmed_at__date__lte=to)
    if q.strip():
        batches = batches.filter(Q(contact__first_name__icontains=q.strip()) | Q(contact__last_name__icontains=q.strip()) | Q(contact__email__icontains=q.strip()) | Q(invoice_number__icontains=q.strip()))
    lines = ClubMembership.objects.filter(registration_details__batch__in=batches).exclude(fee_status=FeeStatus.WAIVED)
    discounted = ExpressionWrapper(Greatest(F("fee_amount") - F("early_payment_discount"), Value(ZERO)), output_field=MONEY)
    due = Case(When(early_payment_deadline__gte=today, early_payment_discount__gt=0, then=discounted), default=F("fee_amount"), output_field=MONEY)
    still_open = ExpressionWrapper(Greatest(due - F("amount_paid"), Value(ZERO)), output_field=MONEY)
    late = Q(registration_details__batch__invoice_due_date__lt=today, registration_details__batch__installment_offsets=[])
    totals = lines.aggregate(
        invoiced=Coalesce(Sum(due), Value(ZERO), output_field=MONEY),
        received=Coalesce(Sum("amount_paid"), Value(ZERO), output_field=MONEY),
        open=Coalesce(Sum(still_open), Value(ZERO), output_field=MONEY),
        overdue=Coalesce(Sum(still_open, filter=late), Value(ZERO), output_field=MONEY),
    )
    overdue_count = lines.annotate(still_open=still_open).filter(late, still_open__gt=0).values("registration_details__batch").distinct().count()
    # Invoices paid in instalments are late by their schedule, not by the invoice date (the same rule as the "overdue" tab of the list).
    plans = batches.exclude(installment_offsets=[]).filter(invoice_due_date__lt=today).prefetch_related("details__membership__payments")
    for batch in plans.iterator(chunk_size=100):
        late_rows = [row for row in installments.batch_schedule(batch, today=today)[0] if row["late"]]
        if late_rows:
            totals["overdue"] += sum((row["remaining"] for row in late_rows), ZERO)
            overdue_count += 1
    return {**totals, "overdue_count": overdue_count}
