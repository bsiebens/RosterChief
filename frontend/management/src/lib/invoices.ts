import type { Me } from '#lib/types.ts';

export { dateTime, day, failed, badPayload, money, parsePayload } from '#lib/shop.ts';

/** The central invoices list (Finance → Invoices): registrations, shop invoices and charges (fines and other costs) in one place. */
export const invoicesOn = (me: Me) => !!(me.can_manage_finance || me.can_manage_charges || (me.features.includes('shop') && me.can_manage_shop_invoices));

export type InvoiceType = 'registration' | 'shop' | 'charge';
export const TYPES: InvoiceType[] = ['registration', 'shop', 'charge'];
export const STATUS_TABS = ['all', 'draft', 'open', 'overdue', 'paid', 'cancelled'] as const;
export type StatusTab = (typeof STATUS_TABS)[number];
export type InvoiceStatus = 'draft' | 'open' | 'partially_paid' | 'overdue' | 'paid' | 'cancelled';

export type InvoiceRow = {
	type: InvoiceType;
	id: string;
	number: string | null;
	member: { id: string; name: string } | null;
	summary: string;
	status: InvoiceStatus;
	total: string;
	amount_paid: string;
	amount_due: string;
	due_date: string | null;
	issued_at: string | null;
	/** The screen of its own kind. */
	href: string;
};
export type TypeSummary = { drafts: number; open: number; overdue: number; paid: number; cancelled: number; open_amount: string; overdue_amount: string };
export type InvoiceList = { count: number; results: InvoiceRow[]; types: InvoiceType[]; summary: Partial<Record<InvoiceType, TypeSummary>> };

export const statusBadge = (s: InvoiceStatus): string => ({ draft: 'badge-neutral', open: 'badge-info', partially_paid: 'badge-warning', overdue: 'badge-error', paid: 'badge-success', cancelled: 'badge-neutral' })[s] ?? 'badge-neutral';
export const typeBadge = (t: InvoiceType): string => ({ registration: 'badge-outline', shop: 'badge-outline', charge: 'badge-outline' })[t] ?? 'badge-outline';
