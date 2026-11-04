"""Request / response schemas for ``/api/v1/memberships``. Money is ``Decimal`` and serialized as a string."""

import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ninja import Field, Schema

from members.services import memberships as fees


class SeasonOut(Schema):
    id: UUID
    name: str
    start_date: datetime.date
    end_date: datetime.date
    is_current: bool = False


class MemberRefOut(Schema):
    id: UUID
    name: str
    email: str
    license_number: str | None = None


class MembershipOut(Schema):
    """Admin view of a membership, fee data included."""

    scope: Literal["admin"] = "admin"
    id: UUID
    member: MemberRefOut
    season: SeasonOut
    status: str
    fee_status: str
    fee_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    remaining_balance: Decimal
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal
    signed_up_at: datetime.date | None = None
    activated_at: datetime.date | None = None
    open_requirement_count: int

    @staticmethod
    def resolve_member(obj):
        m = obj.member
        return {"id": m.pk, "name": m.get_full_name(), "email": m.email, "license_number": m.license_number}

    @staticmethod
    def resolve_amount_due(obj):
        return fees.amount_due(obj)

    @staticmethod
    def resolve_remaining_balance(obj):
        # amount_paid is kept equal to the sum of the payments by the fee service; using it avoids a query per row.
        if obj.fee_status == "waived":
            return Decimal("0.00")
        return max(fees.amount_due(obj) - obj.amount_paid, Decimal("0.00"))

    @staticmethod
    def resolve_open_requirement_count(obj):
        annotated = getattr(obj, "open_count", None)
        return annotated if annotated is not None else obj.open_requirement_count


class MembershipOwnOut(Schema):
    """What a member sees of their own membership: status only, never fee data."""

    scope: Literal["self"]
    id: UUID
    season: SeasonOut
    status: str
    activated_at: datetime.date | None = None


class MembershipCreateIn(Schema):
    member_id: UUID
    season_id: UUID
    fee_amount: Decimal = Decimal("0.00")
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal = Decimal("0.00")
    status: str = "pending"
    signed_up_at: datetime.date | None = None


class MembershipPatchIn(Schema):
    status: str | None = None
    fee_amount: Decimal | None = None
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal | None = None
    signed_up_at: datetime.date | None = None


class WaiveIn(Schema):
    waived: bool = True


class SummaryOut(Schema):
    season: SeasonOut | None = None
    total: int
    paid: int
    partially_paid: int
    unpaid: int
    waived: int
    paid_rate: float


class PaymentOut(Schema):
    id: UUID
    amount: Decimal
    method: str
    reference: str
    paid_at: datetime.datetime
    note: str
    recorded_by: str | None = None

    @staticmethod
    def resolve_recorded_by(obj):
        u = obj.recorded_by
        return (u.get_full_name() or u.email) if u else None


class PaymentIn(Schema):
    amount: Decimal
    method: str = "bank_transfer"
    reference: str = ""
    paid_at: datetime.datetime | None = None
    note: str = ""


class MarkPaidIn(Schema):
    method: str = "other"
    reference: str = ""
    paid_at: datetime.datetime | None = None
    note: str = ""


class RequirementOut(Schema):
    id: UUID
    name: str
    description: str
    requires_document: bool
    blocked_event_kinds: list[str]
    is_active: bool


class RequirementIn(Schema):
    name: str
    description: str = ""
    requires_document: bool = False
    blocked_event_kinds: list[str] = Field(default_factory=list)
    is_active: bool = True


class RequirementPatchIn(Schema):
    name: str | None = None
    description: str | None = None
    requires_document: bool | None = None
    blocked_event_kinds: list[str] | None = None
    is_active: bool | None = None


class ChecklistItemOut(Schema):
    requirement: RequirementOut
    state: Literal["open", "complete", "bypassed"]
    note: str = ""
    completed_at: datetime.datetime | None = None
    completed_by: str | None = None
    has_document: bool = False

    @staticmethod
    def resolve_note(obj):
        return obj.status.note if obj.status else ""

    @staticmethod
    def resolve_completed_at(obj):
        return obj.status.completed_at if obj.status else None

    @staticmethod
    def resolve_completed_by(obj):
        u = obj.status.completed_by if obj.status else None
        return (u.get_full_name() or u.email) if u else None

    @staticmethod
    def resolve_has_document(obj):
        return bool(obj.status and obj.status.document)


class BypassIn(Schema):
    note: str = ""


class AuditEntryOut(Schema):
    id: UUID
    created: datetime.datetime
    actor: str | None = None
    action: str
    target_type: str
    target_id: str
    target_label: str
    changes: dict

    @staticmethod
    def resolve_actor(obj):
        u = obj.actor
        return (u.get_full_name() or u.email) if u else None
