"""Schemas of the management shop-orders slice (api/panel/routers/shop_orders.py).

Money is a decimal string, timestamps ISO 8601, enums their stored values (API.md §6).
"""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

PaymentStatusValue = Literal["pending", "partially_paid", "paid", "refunded"]
FulfillmentStatusValue = Literal["not_ready", "ready_for_pickup", "delivered", "cancelled"]
ProductionStatusValue = Literal["pending", "in_production", "received"]
PaymentMethodValue = Literal["credit_card", "bank_transfer", "cash", "voucher"]
#: Voucher is deliberately not offered by the quick "mark paid" actions (it needs a voucher and an amount: use add payment).
QuickMethodValue = Literal["credit_card", "bank_transfer", "cash"]
PaymentRecordStatusValue = Literal["pending", "confirmed", "failed", "refunded"]


class OrderItemOut(Schema):
    id: uuid.UUID
    number: str
    purchaser_id: uuid.UUID
    purchaser_name: str
    payment_status: PaymentStatusValue
    fulfillment_status: FulfillmentStatusValue
    production_status: ProductionStatusValue = Field(description="Only meaningful when `has_production_lines`.")
    has_production_lines: bool = Field(description="The order has merchandise lines, so `production_status` applies.")
    total: Decimal
    is_closed: bool = Field(description="Delivered and paid: hidden from the default list.")
    created: datetime.datetime


class OrderListOut(Schema):
    items: list[OrderItemOut]
    next_cursor: str | None = Field(description="Opaque; pass as `cursor` for the next page. Newest first.")


class ReadyOrderOut(OrderItemOut):
    has_personalized_lines: bool = Field(description="The order had personalized lines, which are received one at a time.")


class ReadyOrderListOut(Schema):
    items: list[ReadyOrderOut]
    next_cursor: str | None = Field(description="Oldest first.")


class ProductionProductOut(Schema):
    id: uuid.UUID
    name: str
    pending_count: int = Field(description="Lines not yet sent to a manufacturer (cancelled orders excluded).")
    in_production_count: int


class ShopOrdersSummaryOut(Schema):
    open_orders: int
    total_orders: int
    total_sold: Decimal
    items_sold: int
    production_products: list[ProductionProductOut] = Field(description="Every merchandise product: the choices of the production export and reprint.")


class OrderLineOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    is_merchandise: bool = Field(description="`production_status` applies.")
    personalization_enabled: bool
    variant_id: uuid.UUID | None
    variant_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    beneficiary_id: uuid.UUID | None
    beneficiary_name: str
    team_id: uuid.UUID | None
    team_name: str
    personalization_number: str
    personalization_name: str
    production_status: ProductionStatusValue


class OrderPaymentOut(Schema):
    id: uuid.UUID
    amount: Decimal
    method: PaymentMethodValue
    status: PaymentRecordStatusValue
    reference: str
    paid_at: datetime.datetime | None
    voucher_id: uuid.UUID | None
    voucher_number: str


class AppliedDiscountOut(Schema):
    id: uuid.UUID
    discount_id: uuid.UUID
    name: str
    description: str
    amount: Decimal = Field(description="Euros taken off the order.")


class OrderInvoiceOut(Schema):
    id: uuid.UUID
    number: str
    issued_at: datetime.datetime
    due_date: datetime.date | None


class OrderDetailOut(OrderItemOut):
    amount_paid: Decimal = Field(description="Sum of the confirmed payments.")
    amount_due: Decimal = Field(description="What is still to collect; never negative.")
    pickup_instructions: str
    modified: datetime.datetime
    invoice: OrderInvoiceOut | None = Field(description="Download the PDF at `/manage/shop/orders/{id}/invoice/pdf`.")
    lines: list[OrderLineOut]
    payments: list[OrderPaymentOut]
    applied_discounts: list[AppliedDiscountOut]


class MarkPaidIn(Schema):
    method: QuickMethodValue = "cash"
    reference: str = ""


class MarkReadyIn(Schema):
    pickup_instructions: str = Field("", description="Optional; kept on the order and sent to the purchaser.")


class BulkMarkPaidIn(Schema):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=500, description="Ids of other clubs' orders and cancelled orders are ignored; orders with nothing due are skipped.")
    method: QuickMethodValue = "cash"


class BulkMarkReadyIn(MarkReadyIn):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=500, description="Ids of other clubs' orders and cancelled orders are ignored.")


class BulkOut(Schema):
    updated: int = Field(description="In the job's `result`.")


class LineUpdateIn(Schema):
    """Only the fields sent change. Personalization fields apply only to products with personalization enabled, `production_status` only to merchandise."""

    variant_id: uuid.UUID | None = None
    beneficiary_id: uuid.UUID | None = None
    quantity: int | None = Field(None, ge=1)
    personalization_number: str | None = None
    personalization_name: str | None = None
    production_status: ProductionStatusValue | None = None


class AddPaymentIn(Schema):
    amount: Decimal = Field(description="At least 0.01; may be less than what is due (partial settlement).")
    method: PaymentMethodValue = "cash"
    voucher_id: uuid.UUID | None = Field(None, description="Required for (and only for) the `voucher` method; a usable voucher of the purchaser's family or unassigned.")
    reference: str = ""


class PaymentUpdateIn(Schema):
    """Method and reference only: the amount is fixed (delete the payment and add it again). A non-voucher payment cannot become a voucher one."""

    method: PaymentMethodValue | None = None
    reference: str | None = None


class PaymentAddedOut(Schema):
    payment: OrderPaymentOut
    order: OrderDetailOut


class ProductionIn(Schema):
    product_ids: list[uuid.UUID] = Field(min_length=1, description="Merchandise products of this club; others are ignored.")


class ExportResultOut(Schema):
    """The `result` of the export job."""

    token: str = Field(description="Download once at `GET /manage/shop/orders/export-production/{token}` (valid for 5 minutes).")
    filename: str
    lines: int = Field(description="Lines marked in production.")


class ReceiveVariantOut(Schema):
    variant_id: uuid.UUID | None
    variant_name: str
    expected_quantity: int


class ReceiveProductOut(Schema):
    product_id: uuid.UUID
    product_name: str
    personalized_count: int = Field(description="Personalized lines in production: received one by one (`.../lines/{id}/mark-received`), never here.")
    variants: list[ReceiveVariantOut]


class ReceiveSummaryOut(Schema):
    items: list[ReceiveProductOut]


class ReceiptIn(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = Field(None, description="Null for a product without variants.")
    quantity: int = Field(ge=0, description="The physical count that arrived.")


class ReceiveIn(Schema):
    receipts: list[ReceiptIn] = Field(min_length=1)


class ReceiptResultOut(Schema):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    expected_quantity: int
    received_quantity: int
    shortfall: int
    surplus: int
    has_discrepancy: bool
    allocated_line_ids: list[uuid.UUID] = Field(description="Lines marked received (oldest orders first).")
    remaining_line_ids: list[uuid.UUID] = Field(description="Lines the count could not cover; left in production.")


class ReceiveResultOut(Schema):
    results: list[ReceiptResultOut]
    allocated_lines: int
    discrepancies: int
