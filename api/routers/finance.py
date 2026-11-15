"""Finance beyond registrations. ``/finance/open-members``: memberships with a fee still open that did not come from a registration (a member
import, someone added by hand, an older sign-up), and ``/finance/memberships/{id}/payments`` to record a payment (also with a shop voucher) on one.
Everything needs ``registration.manage_finance``. Registration invoices are on ``/registrations``."""

import datetime
import uuid
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.schemas_registrations import PersonRef
from members.models import ClubMembership, FeePayment
from members.services import access
from members.services import memberships as fees
from members.services.errors import Invalid
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
