"""Schemas for /shop orders, payments, production, invoices, settings and stats. Money is a string with 2 decimals."""

import datetime
import uuid
from decimal import Decimal
from typing import Annotated

from ninja import Field, Schema
from pydantic import PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str)]
MoneyIn = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]


class Ref(Schema):
    id: uuid.UUID
    name: str


class NumberRef(Schema):
    id: uuid.UUID
    number: str


# --- orders -------------------------------------------------------------------------------------------------------------


class LineIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int = Field(1, ge=1, le=999)
    beneficiary_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    personalization_number: str = Field("", max_length=20)
    personalization_name: str = Field("", max_length=100)


class VoucherUseIn(Schema):
    number: str
    amount: MoneyIn = Field(gt=0)


class OrderIn(Schema):
    purchaser_id: uuid.UUID
    lines: list[LineIn]
    discount_code: str = ""
    notes: str = ""
    vouchers: list[VoucherUseIn] = []


class PreviewIn(Schema):
    purchaser_id: uuid.UUID
    lines: list[LineIn]
    discount_code: str = ""


class OrderPatch(Schema):
    notes: str | None = None
    pickup_instructions: str | None = None


class LinePatch(Schema):
    quantity: int | None = Field(None, ge=1, le=999)
    variant_id: uuid.UUID | None = None
    beneficiary_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    personalization_number: str | None = Field(None, max_length=20)
    personalization_name: str | None = Field(None, max_length=100)


class ReadyIn(Schema):
    pickup_instructions: str | None = None


class BulkPaidIn(Schema):
    order_ids: list[uuid.UUID]
    method: str


class BulkReadyIn(Schema):
    order_ids: list[uuid.UUID]
    pickup_instructions: str = ""


class CountOut(Schema):
    count: int


class OrderLineOut(Schema):
    id: uuid.UUID
    product: Ref
    variant: Ref | None
    quantity: int
    unit_price: Money
    line_total: Money
    beneficiary: Ref | None
    team: Ref | None
    personalization_number: str
    personalization_name: str
    production_status: str | None
    received_quantity: int
    production_batch: NumberRef | None


class PaymentOut(Schema):
    id: uuid.UUID
    amount: Money
    method: str
    status: str
    reference: str
    note: str
    paid_at: datetime.datetime | None
    provider: str
    payment_url: str
    voucher: NumberRef | None
    recorded_by: str | None
    created: datetime.datetime


class AppliedDiscountOut(Schema):
    id: uuid.UUID
    discount_id: uuid.UUID
    description: str
    discount_type: str
    discount_amount: Money
    amount: Money


class OrderItemOut(Schema):
    id: uuid.UUID
    number: str
    purchaser: Ref
    payment_status: str
    fulfillment_status: str
    production_status: str | None
    member_status: str
    total: Money
    amount_paid: Money
    amount_due: Money
    line_count: int
    notes: str
    pickup_instructions: str
    is_closed: bool
    #: All items are there (in stock or received) but nobody marked it ready yet: it waits in the ready-for-pickup workflow.
    ready_to_confirm: bool
    invoice: NumberRef | None
    created: datetime.datetime


class OrderListOut(Schema):
    count: int
    results: list[OrderItemOut]


class OrderDetailOut(OrderItemOut):
    lines: list[OrderLineOut]
    payments: list[PaymentOut]
    applied_discounts: list[AppliedDiscountOut]
    subtotal: Money
    discount_total: Money
    created_by: str | None
    lines_editable: bool
    online_payments_available: bool
    payment_instructions: str
    default_pickup_instructions: str
    can_cancel: bool
    can_override_cancel: bool
    cancel_blocked_reason: str
    can_edit_discounts: bool
    can_apply_voucher: bool
    #: Confirmed voucher payments: cancelling can put these amounts back on the vouchers (``refund_vouchers``).
    voucher_refund_count: int
    voucher_refund_total: Money


class PreviewLineOut(Schema):
    unit_price: Money
    line_total: Money


class PreviewDiscountOut(Schema):
    description: str
    amount: Money


class PreviewOut(Schema):
    lines: list[PreviewLineOut]
    subtotal: Money
    discount: PreviewDiscountOut | None
    total: Money


# --- payments -----------------------------------------------------------------------------------------------------------


class PaymentIn(Schema):
    amount: MoneyIn = Field(gt=0)
    method: str
    reference: str = ""
    note: str = ""
    voucher_number: str | None = None
    paid_at: datetime.datetime | None = None


class DiscountCodeIn(Schema):
    code: str = Field(..., max_length=100)


class AvailableVoucherOut(Schema):
    id: uuid.UUID
    number: str
    #: ``own`` (issued to the purchaser), ``beneficiary`` (issued to someone the order is for), ``family`` (issued to someone in the
    #: family of the purchaser or of a beneficiary), ``general`` (issued to nobody).
    scope: str
    issued_to: Ref | None
    available_amount: Money
    expiry_date: datetime.date
    notes: str


class VoucherApplyIn(Schema):
    number: str = Field(..., max_length=100)
    #: Default: the voucher's available balance, capped at the amount due.
    amount: MoneyIn | None = Field(None, gt=0)


class PaymentPatch(Schema):
    amount: MoneyIn | None = Field(None, gt=0)
    method: str | None = None
    reference: str | None = None
    note: str | None = None
    paid_at: datetime.datetime | None = None


class MarkPaidIn(Schema):
    method: str
    reference: str = ""


class PaymentLinkIn(Schema):
    return_url: str = Field(max_length=500)
    amount: MoneyIn | None = Field(None, gt=0)


class ProviderOut(Schema):
    key: str
    label: str
    credential_fields: list[str]
    supports_refunds: bool


class NumberingOut(Schema):
    kind: str
    pattern: str
    default_pattern: str
    #: The number the next document of this type gets, and its sequence value.
    next_number: str
    next_sequence: int
    #: ``monthly``, ``yearly`` or ``never``: how often the counter starts again, and the scope it is in now (e.g. ``2026``).
    resets: str
    scope_label: str


class PlaceholderOut(Schema):
    token: str
    meaning: str


class NumberingIn(Schema):
    pattern: str | None = Field(None, max_length=40)
    #: Make this the sequence value of the next number (forward only).
    next: int | None = Field(None, ge=1, le=99_999_999)


class NumberingPreviewIn(Schema):
    kind: str
    pattern: str = Field("", max_length=200)


class NumberingPreviewOut(Schema):
    valid: bool
    message: str
    number: str
    resets: str
    scope_label: str


class SettingsOut(Schema):
    shop_open: bool
    closed_message: str
    pickup_instructions: str
    club_payment_instructions: str
    numbering: list[NumberingOut]
    numbering_placeholders: list[PlaceholderOut]
    #: Invoice numbers must run without surprises: only whoever manages the whole shop (``shop.manage_shop``) may change their pattern or counter.
    can_edit_invoice_numbering: bool
    payment_provider: str
    credentials_configured: bool
    credential_fields: list[str]
    invoice_due_days: int
    providers: list[ProviderOut]


class SettingsIn(Schema):
    shop_open: bool | None = None
    closed_message: str | None = Field(None, max_length=2000)
    pickup_instructions: str | None = Field(None, max_length=2000)
    #: Per document type (``order``, ``invoice``, ``voucher``, ``batch``): a new pattern and/or the next sequence value.
    numbering: dict[str, NumberingIn] | None = None
    payment_provider: str | None = None
    credentials: dict[str, str] | None = None
    invoice_due_days: int | None = Field(None, ge=0, le=365)


class ShopStatusOut(Schema):
    open: bool
    message: str


class WebhookOut(Schema):
    result: str


# --- production ---------------------------------------------------------------------------------------------------------


class ToOrderLineOut(Schema):
    id: uuid.UUID
    order: NumberRef
    purchaser: Ref
    product: Ref
    variant: Ref | None
    quantity: int
    beneficiary: Ref | None
    team: Ref | None
    personalization_number: str
    personalization_name: str
    order_created: datetime.datetime


class ToOrderListOut(Schema):
    count: int
    results: list[ToOrderLineOut]


class BatchLineOut(Schema):
    id: uuid.UUID
    order: NumberRef
    purchaser: Ref
    product: Ref
    variant: Ref | None
    quantity: int
    received_quantity: int
    production_status: str
    beneficiary: Ref | None
    team: Ref | None
    personalization_number: str
    personalization_name: str


class BatchItemOut(Schema):
    id: uuid.UUID
    number: str
    reference: str
    supplier: str
    note: str
    status: str
    line_count: int
    quantity: int
    received_quantity: int
    ordered_at: datetime.datetime | None
    received_at: datetime.datetime | None
    created: datetime.datetime


class BatchDetailOut(BatchItemOut):
    lines: list[BatchLineOut]


class BatchListOut(Schema):
    count: int
    results: list[BatchItemOut]


class BatchIn(Schema):
    line_ids: list[uuid.UUID]
    reference: str = ""
    supplier: str = ""
    note: str = ""


class BatchPatch(Schema):
    reference: str | None = None
    supplier: str | None = None
    note: str | None = None


class ReceiveItemIn(Schema):
    line_id: uuid.UUID
    quantity: int = Field(ge=1)


class ReceiveIn(Schema):
    items: list[ReceiveItemIn] | None = None


# --- invoices -----------------------------------------------------------------------------------------------------------


class InvoiceCreateIn(Schema):
    due_date: datetime.date | None = None


class InvoiceItemOut(Schema):
    id: uuid.UUID
    number: str
    order: NumberRef
    purchaser: Ref
    issued_at: datetime.datetime
    due_date: datetime.date | None
    total: Money
    amount_paid: Money
    amount_due: Money
    payment_status: str
    is_overdue: bool


class InvoiceLineOut(Schema):
    description: str
    quantity: int
    unit_price: Money
    line_total: Money


class InvoiceDiscountOut(Schema):
    description: str
    amount: Money


class InvoiceDetailOut(InvoiceItemOut):
    billing_snapshot: dict | None
    lines: list[InvoiceLineOut]
    discounts: list[InvoiceDiscountOut]


class InvoiceListOut(Schema):
    count: int
    results: list[InvoiceItemOut]


class StatsOut(Schema):
    orders_total: int
    orders_open: int
    revenue: Money
    items_sold: int
    outstanding: Money
    unpaid_count: int
    ready_for_pickup_count: int
    lines_to_order_count: int


# --- ready for pickup ----------------------------------------------------------------------------------------------------


class PickupOrderOut(Schema):
    id: uuid.UUID
    number: str
    purchaser: Ref
    beneficiaries: list[str]
    line_count: int
    quantity: int
    total: Money
    amount_due: Money
    payment_status: str
    created: datetime.datetime


class PickupOut(Schema):
    orders: list[PickupOrderOut]
    pickup_instructions: str
    payment_instructions: str


class PickupConfirmIn(Schema):
    order_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    pickup_instructions: str = Field("", max_length=2000)
    payment_instructions: str = Field("", max_length=2000)
    save_as_default: bool = False


class PickupConfirmOut(Schema):
    count: int
    skipped: int


# --- delivery desk --------------------------------------------------------------------------------------------------------


class DeliveryItemOut(Schema):
    label: str
    quantity: int
    personalization: str


class DeliveryOrderOut(Schema):
    id: uuid.UUID
    number: str
    purchaser: Ref
    beneficiaries: list[str]
    items: list[DeliveryItemOut]
    total: Money
    amount_paid: Money
    amount_due: Money
    payment_status: str
    pickup_instructions: str
    ready_since: datetime.datetime
    invoice: NumberRef | None


class DeliveryCountsOut(Schema):
    all: int
    unpaid: int
    paid: int


class DeliveryOut(Schema):
    orders: list[DeliveryOrderOut]
    counts: DeliveryCountsOut


class HandOverIn(Schema):
    method: str | None = None
    reference: str = Field("", max_length=255)
    allow_unpaid: bool = False


class HandOverOut(Schema):
    order: OrderDetailOut
