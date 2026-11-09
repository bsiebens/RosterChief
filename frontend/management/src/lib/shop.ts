/**
 * Shop catalog, inventory, discounts and vouchers (`/shop/*`): the shapes the API returns plus the helpers the pages
 * share. Orders, payments and invoices live in shop-orders.ts. Money travels as decimal strings ("12.50").
 * List endpoints answer `{count, results}`.
 */
import { fail } from '@sveltejs/kit';
import { getLang } from '#lib/i18n.svelte.ts';
import type { ApiCall } from '#lib/management-api.ts';
import type { Me } from '#lib/types.ts';

export type { PagedList } from '#lib/forms.ts';
export { asList } from '#lib/forms.ts';

/** The shop is on for the club (feature) and the user holds the given flag. */
export const shopOn = (me: Me, flag: 'can_manage_shop_products' | 'can_manage_shop_inventory' | 'can_manage_shop_discounts') => me.features.includes('shop') && !!me[flag];

export type ProductType = 'merchandise' | 'membership' | 'event_fee';
export const PRODUCT_TYPES: ProductType[] = ['merchandise', 'membership', 'event_fee'];

export type Category = { id: string; name: string; product_count: number };
export type CategoryRef = { id: string; name: string };

export type Variant = {
	id: string;
	product_id: string;
	name: string;
	price: string | null;
	effective_price: string;
	is_active: boolean;
	ordering: number;
	stock: number | null;
	in_stock: boolean;
};
export type ProductItem = {
	id: string;
	name: string;
	slug: string;
	category: CategoryRef | null;
	description: string;
	image_url: string | null;
	price: string;
	product_type: ProductType;
	is_active: boolean;
	is_public: boolean;
	personalization_enabled: boolean;
	inventory_managed: boolean;
	variant_count: number;
	in_stock: boolean;
	created: string;
};
export type ProductImageItem = { id: string; url: string; is_main: boolean; ordering: number; alt: string };
export type ProductAttachmentItem = { id: string; title: string; name: string; url: string; size: number; content_type: string; created: string };
/** Registration pricing of a membership product (the API sends nothing useful for other types). */
export type RegistrantTier = { min_registrants: number; discount_type: 'percentage' | 'fixed_amount'; discount_amount: string };
export type RegistrationPricing = { season_id: string | null; season_name: string | null; early_bird_discount_type: '' | 'percentage' | 'fixed_amount'; early_bird_discount_amount: string; early_bird_deadline: string | null; registrant_discount_scope: 'per_person' | 'per_order'; registrant_tiers: RegistrantTier[] };
export type ProductDetail = ProductItem & Partial<RegistrationPricing> & { variants: Variant[]; order_line_count: number; images?: ProductImageItem[]; attachments?: ProductAttachmentItem[] };
export type ProductIn = {
	name: string;
	category_id: string | null;
	description: string;
	price: string;
	product_type: ProductType;
	is_active: boolean;
	is_public: boolean;
	personalization_enabled: boolean;
	inventory_managed: boolean;
	// Membership products only (the form hides them for other types and then sends them cleared).
	season_id: string | null;
	early_bird_discount_type: '' | 'percentage' | 'fixed_amount';
	early_bird_discount_amount: string;
	early_bird_deadline: string | null;
	registrant_discount_scope: 'per_person' | 'per_order';
	registrant_tiers: RegistrantTier[];
};
export const emptyProduct = (): ProductIn => ({
	name: '',
	category_id: null,
	description: '',
	price: '',
	product_type: 'merchandise',
	is_active: true,
	is_public: true,
	personalization_enabled: false,
	inventory_managed: false,
	season_id: null,
	early_bird_discount_type: '',
	early_bird_discount_amount: '',
	early_bird_deadline: null,
	registrant_discount_scope: 'per_person',
	registrant_tiers: []
});
export const productInOf = (p: ProductDetail): ProductIn => ({
	name: p.name,
	category_id: p.category?.id ?? null,
	description: p.description,
	price: p.price,
	product_type: p.product_type,
	is_active: p.is_active,
	is_public: p.is_public,
	personalization_enabled: p.personalization_enabled,
	inventory_managed: p.inventory_managed,
	season_id: p.season_id ?? null,
	early_bird_discount_type: p.early_bird_discount_type ?? '',
	early_bird_discount_amount: p.early_bird_discount_type ? String(Number(p.early_bird_discount_amount ?? 0)) : '',
	early_bird_deadline: p.early_bird_deadline ?? null,
	registrant_discount_scope: p.registrant_discount_scope ?? 'per_person',
	registrant_tiers: (p.registrant_tiers ?? []).map((x) => ({ ...x, discount_amount: String(Number(x.discount_amount)) }))
});

/** What goes to the API: the registration pricing only for membership products (anything else is sent cleared, so a type change drops it). */
export const productPayload = (v: ProductIn) => {
	if (v.product_type === 'membership') {
		return {
			...v,
			early_bird_discount_amount: v.early_bird_discount_type ? v.early_bird_discount_amount || '0' : '0',
			early_bird_deadline: v.early_bird_discount_type ? v.early_bird_deadline || null : null
		};
	}
	return { ...v, season_id: null, early_bird_discount_type: '', early_bird_discount_amount: '0', early_bird_deadline: null, registrant_discount_scope: 'per_person', registrant_tiers: [] as RegistrantTier[] };
};
export type VariantIn = { name: string; price: string | null; is_active?: boolean };

export type StockRow = { product_id: string; product_name: string; variant_id: string | null; variant_name: string | null; on_hand: number; committed: number; available: number; category_id?: string | null; category_name?: string | null };
export type CountResult = { applied: number; unchanged: number; total_difference: number; lines: { product_id: string; variant_id: string | null; name: string; previous: number; counted: number; difference: number }[] };
export type MovementReason = 'received' | 'adjustment' | 'order' | 'order_returned';
export const MOVEMENT_REASONS: MovementReason[] = ['received', 'adjustment', 'order', 'order_returned'];
export type Movement = {
	id: string;
	product_id: string;
	product_name: string;
	variant_id: string | null;
	variant_name: string | null;
	quantity: number;
	reason: MovementReason;
	note: string;
	order_id: string | null;
	order_number: string | null;
	created_by: string | null;
	created: string;
};
export type ReceiveIn = { note: string; items: { product_id: string; variant_id: string | null; quantity: number }[] };

export type DiscountType = 'percentage' | 'fixed_amount';
export type DiscountStatus = 'active' | 'scheduled' | 'expired' | 'inactive';
export const DISCOUNT_STATUSES: DiscountStatus[] = ['active', 'scheduled', 'expired', 'inactive'];
export type Discount = {
	id: string;
	name: string;
	slug: string;
	description: string;
	code: string;
	discount_type: DiscountType;
	discount_amount: string;
	is_active: boolean;
	valid_from: string | null;
	valid_until: string | null;
	max_uses: number | null;
	once_per_member: boolean;
	status: DiscountStatus;
	used_count: number;
	created: string;
};
export type DiscountIn = {
	name: string;
	description: string;
	code: string;
	discount_type: DiscountType;
	discount_amount: string;
	is_active: boolean;
	valid_from: string | null;
	valid_until: string | null;
	max_uses: number | null;
	once_per_member: boolean;
};
export const emptyDiscount = (): DiscountIn => ({ name: '', description: '', code: '', discount_type: 'percentage', discount_amount: '', is_active: true, valid_from: null, valid_until: null, max_uses: null, once_per_member: false });
export const discountInOf = (d: Discount): DiscountIn => ({
	name: d.name,
	description: d.description,
	code: d.code,
	discount_type: d.discount_type,
	discount_amount: d.discount_amount,
	is_active: d.is_active,
	valid_from: d.valid_from,
	valid_until: d.valid_until,
	max_uses: d.max_uses,
	once_per_member: d.once_per_member
});

export type VoucherStatus = 'usable' | 'expired' | 'exhausted' | 'inactive';
export const VOUCHER_STATUSES: VoucherStatus[] = ['usable', 'expired', 'exhausted', 'inactive'];
export type Voucher = {
	id: string;
	number: string;
	issued_to: { id: string; name: string } | null;
	amount: string;
	consumed_amount: string;
	available_amount: string;
	expiry_date: string;
	is_active: boolean;
	notes: string;
	status: VoucherStatus;
	created: string;
};
export type VoucherHistory = { kind: 'manual' | 'order'; amount: string; when: string; detail: string; order_id: string | null; consumption_id: string | null; recorded_by: string | null };
export type VoucherDetail = Voucher & { history: VoucherHistory[] };
export type VoucherIn = { amount: string; expiry_date: string; issued_to_id: string | null; notes: string; count?: number };

/** Badge colour per status (daisyUI classes). */
export const statusBadge = (s: DiscountStatus | VoucherStatus): string =>
	({ active: 'badge-success', usable: 'badge-success', scheduled: 'badge-info', expired: 'badge-warning', exhausted: 'badge-warning', inactive: 'badge-warning' })[s];

// -- formatting -------------------------------------------------------------------------------------------------------
export const CURRENCY = 'EUR';
export function money(v: string | number | null | undefined): string {
	if (v === null || v === undefined || v === '') return '';
	const n = typeof v === 'number' ? v : Number(v);
	if (!Number.isFinite(n)) return String(v);
	return new Intl.NumberFormat(getLang(), { style: 'currency', currency: CURRENCY }).format(n);
}
/** A day (YYYY-MM-DD) or datetime in the viewer's language. */
export function day(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
	return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
}
export function dateTime(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(d);
}
/** "Name - Variant" for a stock row or movement. */
export const stockLabel = (r: { product_name: string; variant_name: string | null }) => (r.variant_name ? `${r.product_name} - ${r.variant_name}` : r.product_name);

export const productHref = (id: string) => `/shop/products/${id}`;
export const discountHref = (id: string) => `/shop/discounts/${id}`;
export const voucherHref = (id: string) => `/shop/vouchers/${id}`;

/** Read a JSON payload field posted by a page (the pattern of news and forms). */
export function parsePayload<T>(form: FormData, name = 'payload'): T | null {
	try {
		return JSON.parse(String(form.get(name) ?? '')) as T;
	} catch {
		return null;
	}
}
export const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
/** A failed action result: what the pages read back (`op`, `code`, `fields`, `message`, `status`). */
export const failed = (op: string, res: ApiCall<unknown>, extra: Record<string, unknown> = {}) =>
	fail(httpStatus(res.status), { op, code: res.code ?? '', fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status, ...extra });
export const badPayload = (op: string) => fail(400, { op, code: '', fields: {} as Record<string, string>, message: '', status: 400 });
