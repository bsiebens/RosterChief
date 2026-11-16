/**
 * Shop orders, payments, production, invoices, settings and stats (`/shop/*`, see the orders section of the shop
 * contract). Catalog, inventory, discounts and vouchers live in shop.ts, whose formatting and action helpers are
 * reused here. Money travels as decimal strings ("12.50"); list endpoints answer `{count, results}`.
 */
import type { Me } from '#lib/types.ts';
import type { Ref } from '#lib/events/types.ts';

export { asList, day, dateTime, failed, httpStatus, badPayload, money, parsePayload, type PagedList } from '#lib/shop.ts';

/** The shop is on for the club and the user may manage orders, payments, production and the shop settings. */
export const shopOrdersOn = (me: Me) => me.features.includes('shop') && !!me.can_manage_shop_orders;
/** The shop is on for the club and the user may manage invoices. */
export const shopInvoicesOn = (me: Me) => me.features.includes('shop') && !!me.can_manage_shop_invoices;

export type PaymentStatus = 'pending' | 'partially_paid' | 'paid' | 'refunded';
export type FulfillmentStatus = 'not_ready' | 'ready_for_pickup' | 'delivered' | 'cancelled';
export type ProductionStatus = 'pending' | 'ordered' | 'received';
export const PAYMENT_STATUSES: PaymentStatus[] = ['pending', 'partially_paid', 'paid', 'refunded'];
export const FULFILLMENT_STATUSES: FulfillmentStatus[] = ['not_ready', 'ready_for_pickup', 'delivered', 'cancelled'];

export type OrderItem = {
	id: string;
	number: string;
	purchaser: Ref;
	payment_status: PaymentStatus;
	fulfillment_status: FulfillmentStatus;
	production_status: ProductionStatus | null;
	member_status: string;
	total: string;
	amount_paid: string;
	amount_due: string;
	line_count: number;
	notes: string;
	pickup_instructions: string;
	is_closed: boolean;
	ready_to_confirm: boolean;
	invoice: { id: string; number: string } | null;
	created: string;
};
export type OrderLine = {
	id: string;
	product: Ref;
	variant: Ref | null;
	quantity: number;
	unit_price: string;
	line_total: string;
	beneficiary: Ref | null;
	team: Ref | null;
	personalization_number: string;
	personalization_name: string;
	production_status: ProductionStatus | null;
	received_quantity: number;
	production_batch: { id: string; number: string } | null;
};
export type PaymentMethod = 'cash' | 'bank_transfer' | 'credit_card' | 'voucher' | 'online';
/** The methods staff may record by hand. */
export const MANUAL_METHODS: PaymentMethod[] = ['cash', 'bank_transfer', 'credit_card', 'voucher'];
export type PaymentRowStatus = 'pending' | 'confirmed' | 'failed' | 'refunded';
export type Payment = {
	id: string;
	amount: string;
	method: PaymentMethod;
	status: PaymentRowStatus;
	reference: string;
	note: string;
	paid_at: string | null;
	provider: string;
	payment_url: string;
	voucher: { id: string; number: string } | null;
	recorded_by: string | null;
	created: string;
};
export type AppliedDiscount = { id: string; discount_id: string; description: string; discount_type: string; discount_amount: string; amount: string };
export type OrderDetail = OrderItem & {
	lines: OrderLine[];
	payments: Payment[];
	applied_discounts: AppliedDiscount[];
	subtotal: string;
	discount_total: string;
	created_by: string | null;
	lines_editable: boolean;
	online_payments_available: boolean;
	payment_instructions: string;
	default_pickup_instructions: string;
	can_cancel: boolean;
	cancel_blocked_reason: string;
	can_override_cancel: boolean;
	/** Confirmed voucher payments: cancelling can put these amounts back on the vouchers. */
	voucher_refund_count: number;
	voucher_refund_total: string;
	can_edit_discounts: boolean;
	can_apply_voucher: boolean;
};

export type LineIn = {
	product_id: string;
	variant_id: string | null;
	quantity: number;
	beneficiary_id: string | null;
	personalization_number: string;
	personalization_name: string;
};
export type OrderIn = { purchaser_id: string; lines: LineIn[]; discount_code: string; notes: string; vouchers: { number: string; amount: string }[] };
export type Preview = { lines: { unit_price: string; line_total: string }[]; subtotal: string; discount: { description: string; amount: string } | null; total: string };

export const orderHref = (id: string) => `/shop/orders/${id}`;
export const invoicePdfHref = (id: string) => `/api/v1/shop/invoices/${id}/pdf`;
export const batchCsvHref = (id: string) => `/api/v1/shop/production/batches/${id}/export`;

/** daisyUI badge colour per status. */
export const paymentBadge = (s: PaymentStatus): string => ({ pending: 'badge-neutral', partially_paid: 'badge-warning', paid: 'badge-success', refunded: 'badge-error' })[s];
export const fulfillmentBadge = (s: FulfillmentStatus): string => ({ not_ready: 'badge-neutral', ready_for_pickup: 'badge-info', delivered: 'badge-success', cancelled: 'badge-error' })[s];
export const productionBadge = (s: ProductionStatus): string => ({ pending: 'badge-neutral', ordered: 'badge-info', received: 'badge-success' })[s];
export const rowPaymentBadge = (s: PaymentRowStatus): string => ({ pending: 'badge-neutral', confirmed: 'badge-success', failed: 'badge-error', refunded: 'badge-warning' })[s];

export type BatchStatus = 'draft' | 'ordered' | 'partially_received' | 'received';
export type BatchItem = {
	id: string;
	number: string;
	reference: string;
	supplier: string;
	note: string;
	status: BatchStatus;
	line_count: number;
	quantity: number;
	received_quantity: number;
	ordered_at: string | null;
	received_at: string | null;
	created: string;
};
export type BatchLine = {
	id: string;
	order: { id: string; number: string };
	purchaser: Ref;
	product: Ref;
	variant: Ref | null;
	quantity: number;
	received_quantity: number;
	production_status: ProductionStatus;
	beneficiary: Ref | null;
	team: Ref | null;
	personalization_number: string;
	personalization_name: string;
};
export type BatchDetail = BatchItem & { lines: BatchLine[] };
export type PendingLine = Omit<BatchLine, 'received_quantity' | 'production_status'> & { order_created: string };
export const batchBadge = (s: BatchStatus): string => ({ draft: 'badge-neutral', ordered: 'badge-info', partially_received: 'badge-warning', received: 'badge-success' })[s];

export type InvoiceItem = {
	id: string;
	number: string;
	order: { id: string; number: string };
	purchaser: Ref;
	issued_at: string;
	due_date: string | null;
	total: string;
	amount_paid: string;
	amount_due: string;
	payment_status: PaymentStatus;
	is_overdue: boolean;
};

export type ProviderInfo = { key: string; label: string; credential_fields: string[]; supports_refunds: boolean };
export type NumberingState = { kind: string; pattern: string; default_pattern: string; next_number: string; next_sequence: number; resets: 'monthly' | 'yearly' | 'never'; scope_label: string };
export type NumberingPlaceholder = { token: string; meaning: string };
export type NumberingPreview = { valid: boolean; message: string; number: string; resets: string; scope_label: string };
export type NumberingChanges = Record<string, { pattern?: string; next?: number }>;
export type ShopSettings = { can_edit_invoice_numbering: boolean; numbering: NumberingState[]; numbering_placeholders: NumberingPlaceholder[]; shop_open: boolean; closed_message: string; payment_provider: string; credentials_configured: boolean; credential_fields: string[]; invoice_due_days: number; pickup_instructions: string; club_payment_instructions: string; vat_mode: 'off' | 'included'; can_edit_vat: boolean; providers: ProviderInfo[] };

export type PickupOrder = {
	id: string;
	number: string;
	purchaser: Ref;
	beneficiaries: string[];
	line_count: number;
	quantity: number;
	total: string;
	amount_due: string;
	payment_status: PaymentStatus;
	created: string;
};
export type PickupList = { orders: PickupOrder[]; pickup_instructions: string; payment_instructions: string };

export type DeliveryOrder = {
	id: string;
	number: string;
	purchaser: Ref;
	beneficiaries: string[];
	items: { label: string; quantity: number; personalization: string }[];
	total: string;
	amount_paid: string;
	amount_due: string;
	payment_status: PaymentStatus;
	pickup_instructions: string;
	ready_since: string;
	invoice: { id: string; number: string } | null;
};
export type DeliveryList = { orders: DeliveryOrder[]; counts: { all: number; unpaid: number; paid: number } };

export type ShopStats = {
	orders_total: number;
	orders_open: number;
	revenue: string;
	items_sold: number;
	outstanding: string;
	unpaid_count: number;
	ready_for_pickup_count: number;
	lines_to_order_count: number;
};

/** The line label: "Product - Variant". */
export const lineLabel = (l: { product: Ref; variant: Ref | null }) => (l.variant ? `${l.product.name} - ${l.variant.name}` : l.product.name);
export const personalization = (l: { personalization_number: string; personalization_name: string }) => [l.personalization_number, l.personalization_name].filter(Boolean).join(' · ');

/** Call the API from the browser (through the proxy). Returns the field errors the same way the server helper does. */
export async function browserApi<T>(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; data?: T; message: string; fields: Record<string, string> }> {
	try {
		const res = await fetch(`/api/v1${path}`, { method, headers: { accept: 'application/json', ...(body !== undefined ? { 'content-type': 'application/json' } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
		const json = await res.json().catch(() => undefined);
		if (res.ok) return { ok: true, status: res.status, data: json as T, message: '', fields: {} };
		const fields: Record<string, string> = {};
		if (json?.errors && typeof json.errors === 'object') for (const [k, v] of Object.entries(json.errors)) fields[k] = Array.isArray(v) ? String(v[0]) : String(v);
		return { ok: false, status: res.status, message: typeof json?.detail === 'string' ? json.detail : '', fields };
	} catch {
		return { ok: false, status: 0, message: 'network', fields: {} };
	}
}

/** A usable voucher this order's purchaser can pay with: their own, someone in their family's, or a general one. */
export type AvailableVoucher = { id: string; number: string; scope: 'own' | 'beneficiary' | 'family' | 'general'; issued_to: { id: string; name: string } | null; available_amount: string; expiry_date: string; notes: string };

/** The result of an invoice action (rebuilding the PDF) to show as a note. */
export type InvoiceOutcome = { kind: 'done' | 'failed'; message: string };
