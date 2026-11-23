export { day, money } from '#lib/shop.ts';

export type SponsorBilling = { status: 'none' | 'paid_up' | 'open' | 'overdue' | 'suspended'; overdue_days: number; next_due_date: string | null; open_amount: string };
export type SponsorRow = {
	id: string;
	name: string;
	url: string;
	logo_url: string | null;
	start_date: string;
	end_date: string | null;
	status: 'active' | 'upcoming' | 'ended' | 'suspended';
	billing: SponsorBilling;
};
export type InstallmentStatus = 'scheduled' | 'open' | 'partially_paid' | 'paid' | 'overdue';
export type Installment = { id: string; due_date: string; amount: string; status: InstallmentStatus; overdue_days: number; amount_paid: string; amount_due: string; invoice_number: string | null; order_id: string | null };
export type SponsorAgreement = {
	sponsor_id: string;
	contact_name: string;
	contact_email: string;
	description: string;
	lead_days: number;
	grace_days: number;
	warn_days: number;
	keep_active: boolean;
	keep_active_until: string | null;
	suspended_at: string | null;
	installments: Installment[];
	billing: SponsorBilling;
};
export type SponsorAgreementIn = {
	contact_name: string;
	contact_email: string;
	description: string;
	lead_days: number;
	grace_days: number;
	warn_days: number;
	installments: { id?: string; due_date: string; amount: string }[];
};

export const billingBadge = (s: SponsorBilling['status']): string => ({ none: 'badge-ghost', paid_up: 'badge-success', open: 'badge-info', overdue: 'badge-warning', suspended: 'badge-error' })[s] ?? 'badge-neutral';
export const installmentBadge = (s: InstallmentStatus): string => ({ scheduled: 'badge-neutral', open: 'badge-info', partially_paid: 'badge-warning', paid: 'badge-success', overdue: 'badge-error' })[s] ?? 'badge-neutral';

/** `count` dates `every` months apart starting at `first` (YYYY-MM-DD), the amounts splitting `total` to the cent (the first one takes the rounding rest). */
export function schedule(total: number, count: number, first: string, every: number): { due_date: string; amount: string }[] {
	if (!first || !(count >= 1) || !(total > 0)) return [];
	const [y, m, d] = first.split('-').map(Number);
	const cents = Math.round(total * 100);
	const each = Math.floor(cents / count);
	return Array.from({ length: count }, (_, i) => {
		const when = new Date(Date.UTC(y, m - 1 + i * every, 1));
		const last = new Date(Date.UTC(when.getUTCFullYear(), when.getUTCMonth() + 1, 0)).getUTCDate();
		const date = new Date(Date.UTC(when.getUTCFullYear(), when.getUTCMonth(), Math.min(d, last)));
		const amount = (i === 0 ? cents - each * (count - 1) : each) / 100;
		return { due_date: date.toISOString().slice(0, 10), amount: amount.toFixed(2) };
	});
}
