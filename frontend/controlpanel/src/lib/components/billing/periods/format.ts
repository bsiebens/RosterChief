import type { DueState, DueStatus } from '#lib/billing.ts';

const eur = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'EUR' });
/** Euro amount with cents, for invoices and payments. */
export const money2 = (v: string | number | null | undefined) => eur.format(Number(v ?? 0));

/** A date-only value ("2026-11-01") shown as written, with no timezone shift. */
export const day = (d: string | null | undefined) => {
	if (!d) return '—';
	const [y, m, dd] = d.slice(0, 10).split('-').map(Number);
	return new Date(Date.UTC(y, m - 1, dd)).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
};

export const dateTime = (iso: string | null | undefined) =>
	iso ? new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';

export const METHODS = [
	['bank_transfer', 'Bank transfer'],
	['card', 'Card'],
	['cash', 'Cash'],
	['other', 'Other']
] as const;
export const methodLabel = (m: string) => METHODS.find(([v]) => v === m)?.[1] ?? m;

export const stateTone: Record<DueState, string> = { overdue: 'badge-error', in_grace: 'badge-warning', issued_ahead: 'badge-info', settled: 'badge-success' };
export const stateLabel: Record<DueState, string> = { overdue: 'overdue', in_grace: 'in grace', issued_ahead: 'upcoming', settled: 'settled' };
export const statusLabel: Record<DueStatus, string> = { unpaid: 'unpaid', partial: 'partially paid', paid: 'paid', waived: 'waived', cancelled: 'cancelled' };

export const owing = (s: DueStatus) => s === 'unpaid' || s === 'partial';
export const vatPct = (rate: string) => `${Number(rate)}%`;

/** `YYYY-MM-DD` of today in the browser's timezone. */
export const todayLocal = () => {
	const d = new Date();
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
