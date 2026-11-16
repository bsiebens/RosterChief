/** Pure helpers for the members list page: URL <-> filter state, ordering toggle, badge variants. */

export const PAGE_SIZE = 25;
export const KINDS = ['all', 'member', 'guardian', 'staff', 'none'] as const;
export type Kind = (typeof KINDS)[number];
/** The tab shown when the URL has no `kind`. */
export const DEFAULT_KIND: Kind = 'all';
export const STATUSES = ['active', 'pending', 'lapsed', 'cancelled'] as const;
/** '' (absent from the URL) = the default; 'all' = no status filter on the Members tab. */
const STATUS_PARAMS = [...STATUSES, 'open', 'all'] as const;
export const FEE_STATUSES = ['unpaid', 'partially_paid', 'paid', 'waived'] as const;
export const ORDER_FIELDS = ['last_name', 'first_name', 'date_of_birth', 'license_number', 'email'] as const;
export type OrderField = (typeof ORDER_FIELDS)[number];

export type MemberRow = {
	id: string;
	first_name: string;
	last_name: string;
	email: string;
	date_of_birth: string | null;
	language: string;
	license_number: string | null;
	photo_url: string | null;
	roles: string[];
	family_ids: string[];
	has_login?: boolean;
	families?: { id: string; name: string }[];
	/** The listed season's membership status; fee status for admins only. */
	status?: string | null;
	fee_status?: string | null;
	/** Short names of the teams the person plays on this season (at most 3). */
	teams?: string[];
	/** The season's documents summary; null when they hold no membership then. */
	requirements?: DocCounts | null;
};
export type DocCounts = { missing: number; pending: number; declined: number; expiring_soon: number; expired: number };
export type DocKey = keyof DocCounts;

/** Most urgent first: what the Docs pill shows when something is open. */
export const DOC_URGENCY: DocKey[] = ['expired', 'declined', 'missing', 'pending', 'expiring_soon'];
const DOC_TONE: Record<DocKey, string> = { expired: 'badge-error', declined: 'badge-error', missing: 'badge-warning', pending: 'badge-info', expiring_soon: 'badge-warning' };

/** The compact Docs pill: `ok` when nothing is open, else the most urgent kind with its count. Null without a membership. */
export function docsPill(counts: DocCounts | null | undefined): { key: DocKey | 'ok'; n: number; tone: string } | null {
	if (!counts) return null;
	const key = DOC_URGENCY.find((k) => counts[k] > 0);
	return key ? { key, n: counts[key], tone: DOC_TONE[key] } : { key: 'ok', n: 0, tone: 'badge-success' };
}
export type TeamOption = { id: string; name: string };
export type SeasonOption = { id: string; name: string; is_current: boolean };

export type ListFilters = { q: string; kind: Kind; status: string; fee_status: string; documents: string; team: string; season: string; ordering: string; offset: number };

const oneOf = <T extends string>(v: string | null, allowed: readonly T[]): T | '' => (allowed as readonly string[]).includes(v ?? '') ? (v as T) : '';

export function parseFilters(sp: URLSearchParams): ListFilters {
	const field = (sp.get('ordering') ?? '').replace(/^-/, '');
	const ordering = (ORDER_FIELDS as readonly string[]).includes(field) ? (sp.get('ordering') as string) : 'last_name';
	const offset = Math.max(0, parseInt(sp.get('offset') ?? '0', 10) || 0);
	return {
		q: (sp.get('q') ?? '').trim(),
		kind: oneOf(sp.get('kind'), KINDS) || DEFAULT_KIND,
		status: oneOf(sp.get('status'), STATUS_PARAMS),
		fee_status: oneOf(sp.get('fee_status'), FEE_STATUSES),
		documents: oneOf(sp.get('documents'), ['pending'] as const),
		team: sp.get('team') ?? '',
		season: sp.get('season') ?? '',
		ordering,
		offset
	};
}

/** The Members tab hides lapsed/cancelled by default (the API's `open` = active + pending), like the old panel.
 * Guardians and staff hold no membership, so a status filter would hide them all: it only applies on the Members tab. */
export function apiStatus(f: ListFilters): string | undefined {
	if (f.status === 'all') return undefined;
	if (f.status) return f.status;
	return f.kind === 'member' ? 'open' : undefined;
}

/** Query for GET /api/v1/members. */
export function apiQuery(f: ListFilters): Record<string, string | number | boolean | undefined> {
	return {
		q: f.q || undefined,
		role: f.kind === 'all' ? undefined : f.kind,
		include_guardians: f.kind === 'all' ? true : undefined,
		season: f.season || undefined,
		status: apiStatus(f),
		fee_status: f.fee_status || undefined,
		documents: f.documents || undefined,
		team: f.team || undefined,
		ordering: f.ordering,
		limit: PAGE_SIZE,
		offset: f.offset || undefined
	};
}

/** Page URL (path + query) for a filter state; defaults are omitted. Changing anything but `offset` resets paging. */
export function pageHref(f: ListFilters, patch: Partial<ListFilters> = {}): string {
	const next = { ...f, ...patch };
	if (!('offset' in patch)) next.offset = 0;
	const sp = new URLSearchParams();
	if (next.q) sp.set('q', next.q);
	if (next.kind !== DEFAULT_KIND) sp.set('kind', next.kind);
	if (next.status) sp.set('status', next.status);
	if (next.fee_status) sp.set('fee_status', next.fee_status);
	if (next.documents) sp.set('documents', next.documents);
	if (next.team) sp.set('team', next.team);
	if (next.season) sp.set('season', next.season);
	if (next.ordering !== 'last_name') sp.set('ordering', next.ordering);
	if (next.offset) sp.set('offset', String(next.offset));
	const qs = sp.toString();
	return qs ? `?${qs}` : '?';
}

export const hasActiveFilters = (f: ListFilters) => !!(f.q || f.status || f.fee_status || f.documents || f.team || f.season || f.kind !== DEFAULT_KIND);

/** Ordering after clicking a column: same field flips direction, a new field starts ascending. */
export function toggleOrdering(current: string, field: OrderField): string {
	return current === field ? `-${field}` : field;
}

export function sortState(current: string, field: OrderField): 'ascending' | 'descending' | 'none' {
	if (current === field) return 'ascending';
	if (current === `-${field}`) return 'descending';
	return 'none';
}

export function statusVariant(status: string | null | undefined): string {
	return { active: 'badge-success', pending: 'badge-warning', cancelled: 'badge-error' }[status ?? ''] ?? 'badge-neutral';
}

export function feeVariant(fee: string | null | undefined): string {
	return { paid: 'badge-success', partially_paid: 'badge-warning', unpaid: 'badge-error' }[fee ?? ''] ?? 'badge-ghost';
}

/** Roles worth a badge: only when the person holds more than one. */
export const extraRoles = (roles: string[]) => (roles.length > 1 ? roles : []);

export function errorKey(status: number): string {
	return status === 403 ? 'common.error.forbidden' : status === 404 ? 'common.error.not_found' : status === 0 ? 'common.error.network' : 'common.error.generic';
}
