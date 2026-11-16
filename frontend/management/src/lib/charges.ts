import type { Me } from '#lib/types.ts';
import type { Payment } from '#lib/shop-orders.ts';

export { badPayload, dateTime, day, failed, money, parsePayload } from '#lib/shop.ts';

/** Charge invoices (Finance → Invoices): invoices staff write for amounts that are not shop items, such as a fine. Own permission, no shop needed. */
export const chargesOn = (me: Me) => !!me.can_manage_charges;

export type ChargeStatus = 'draft' | 'sent' | 'partially_paid' | 'overdue' | 'paid' | 'cancelled';
export const TABS = ['all', 'draft', 'open', 'overdue', 'paid', 'cancelled'] as const;
export type ChargeTab = (typeof TABS)[number];

export type ChargeItem = {
	id: string;
	status: ChargeStatus;
	/** The invoice number once sent; a draft has none yet. */
	number: string | null;
	member: { id: string; name: string };
	summary: string;
	total: string;
	amount_paid: string;
	amount_due: string;
	due_date: string | null;
	issued_at: string | null;
	created: string;
	reminders_sent: number;
};
export type ChargeSummary = { drafts: number; open: number; overdue: number; paid: number; cancelled: number; open_amount: string; overdue_amount: string };
export type ChargeList = { count: number; results: ChargeItem[]; summary: ChargeSummary };
export type ChargeLine = { id: string; description: string; quantity: number; unit_price: string; line_total: string };
export type ChargeDetail = ChargeItem & {
	lines: ChargeLine[];
	notes: string;
	/** Printed on the invoice and repeated in the email. */
	invoice_text: string;
	payments: Payment[];
	last_reminder_at: string | null;
	is_draft: boolean;
	can_cancel: boolean;
	can_remind: boolean;
	can_record_payment: boolean;
	recipient_email: string;
};
export type ChargeSettings = { reminders_enabled: boolean; after_days: number; repeat_days: number; max_reminders: number; invoice_due_days: number };

export type LineIn = { description: string; quantity: number; unit_price: string };
export type ChargeIn = { member_id: string; lines: LineIn[]; due_date: string | null; notes: string; invoice_text: string };

export const chargeHref = (id: string) => `/finance/charges/${id}`;
export const chargePdfHref = (id: string) => `/api/v1/finance/charges/${id}/pdf`;

export const statusBadge = (s: ChargeStatus): string => ({ draft: 'badge-neutral', sent: 'badge-info', partially_paid: 'badge-warning', overdue: 'badge-error', paid: 'badge-success', cancelled: 'badge-neutral' })[s] ?? 'badge-neutral';
