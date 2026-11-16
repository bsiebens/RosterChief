// Small formatters for the billing sub-pages. Dates from the API are plain days ("2026-11-02"), shown as such in UTC.

export const day = (d: string | null | undefined) => (d ? new Date(`${d}T00:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '—');

/** Whole days from today (the browser's calendar day) to `d`; negative = in the past. */
export function daysFromToday(d: string): number {
	const n = new Date();
	const today = Date.UTC(n.getFullYear(), n.getMonth(), n.getDate());
	return Math.round((new Date(`${d}T00:00:00Z`).getTime() - today) / 86_400_000);
}

/** "today", "in 12 days", "3 days ago", "in 2 months". */
export function relative(d: string | null | undefined): string {
	if (!d) return '';
	const n = daysFromToday(d);
	if (n === 0) return 'today';
	if (n === 1) return 'tomorrow';
	if (n === -1) return 'yesterday';
	const a = Math.abs(n);
	const t = a < 60 ? `${a} days` : a < 730 ? `${Math.round(a / 30.4)} months` : `${Math.round(a / 365)} years`;
	return n > 0 ? `in ${t}` : `${t} ago`;
}

/** Prices keep their cents (unlike dashboard money()): "50.00". */
export const price = (v: string | number | null | undefined) => (v == null || v === '' ? '—' : Number(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export const newKey = () => crypto.randomUUID();
