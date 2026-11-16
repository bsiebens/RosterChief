import { getLang, t } from '#lib/i18n.svelte.ts';
import type { ClubMembership, FormValues, HistoryEntry, MemberDetail, Season } from './detail-types.ts';

/** `t(prefix + value)` when the catalog knows it, else the raw value (so unknown codes stay visible). */
function labelOr(prefix: string, value: string): string {
	const key = prefix + value;
	const label = t(key);
	return label === key ? value : label;
}

export const roleLabel = (role: string) => labelOr('members.detail.role.', role);
export const familyRoleLabel = (role: string) => labelOr('members.detail.family_role.', role);
export const statusLabel = (status: string) => labelOr('members.detail.status.', status);
export const feeStatusLabel = (status: string) => labelOr('members.detail.fee.', status);

export function statusBadge(status: string): string {
	return { active: 'badge-success', pending: 'badge-warning', cancelled: 'badge-error' }[status] ?? 'badge-neutral';
}
export function feeBadge(status: string): string {
	return { paid: 'badge-success', partially_paid: 'badge-warning', unpaid: 'badge-error' }[status] ?? 'badge-ghost';
}

export const fullName = (m: { first_name: string; last_name: string }) => `${m.first_name} ${m.last_name}`.trim();

/** Locale-aware date (`2026-11-03` -> "3 Nov 2026"); returns the input when it cannot be parsed. */
export function formatDate(value: string | null | undefined): string {
	if (!value) return '';
	const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
	return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
}

export function formatDateTime(value: string): string {
	const d = new Date(value);
	return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(getLang(), { dateStyle: 'medium', timeStyle: 'short' }).format(d);
}

/** `tel:` href: keeps digits and a leading plus only. */
export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, '')}`;

export function seasonName(seasons: Season[], id: string): string {
	return seasons.find((s) => s.id === id)?.name ?? '';
}

/** The membership of the season that covers today, if any. */
export function currentMembership(memberships: ClubMembership[], seasons: Season[]): ClubMembership | undefined {
	const current = seasons.find((s) => s.is_current);
	return current ? memberships.find((m) => m.season_id === current.id) : undefined;
}

/** Whether to show the "guardian"/"staff" info alert (the person holds that role but has no membership this season). */
export function roleInfoKeys(member: Pick<MemberDetail, 'roles'>, hasMembership: boolean): string[] {
	if (hasMembership) return [];
	const keys: string[] = [];
	if (member.roles.includes('guardian')) keys.push('members.detail.guardian_info');
	if (member.roles.includes('staff')) keys.push('members.detail.staff_info');
	return keys;
}

// -- history --------------------------------------------------------------------------------------------------------

export const actionLabel = (action: string) => labelOr('members.history.action.', action);

/** Render any JSON value as plain text (the caller prints it as text, never as HTML). */
export function formatValue(value: unknown): string {
	if (value === null || value === undefined || value === '') return t('members.history.empty_value');
	if (typeof value === 'boolean') return value ? t('common.yes') : t('common.no');
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

export type DiffRow = { field: string; from: string | null; to: string | null; plain?: string };

/** `changes` is `{field: {from, to}}`; anything else is shown as `field: value`. */
export function diffRows(changes: HistoryEntry['changes'] | null | undefined): DiffRow[] {
	if (!changes || typeof changes !== 'object') return [];
	return Object.entries(changes).map(([field, v]) => {
		if (v && typeof v === 'object' && !Array.isArray(v) && ('from' in v || 'to' in v)) {
			const d = v as { from?: unknown; to?: unknown };
			return { field, from: formatValue(d.from), to: formatValue(d.to) };
		}
		return { field, from: null, to: null, plain: formatValue(v) };
	});
}

// -- form ------------------------------------------------------------------------------------------------------------

export const LANGUAGES = ['en', 'nl', 'fr'] as const;

export function formValuesFrom(m?: Partial<MemberDetail>): FormValues {
	return {
		first_name: m?.first_name ?? '',
		last_name: m?.last_name ?? '',
		email: m?.email ?? '',
		phone: m?.phone ?? '',
		emergency_phone: m?.emergency_phone ?? '',
		date_of_birth: m?.date_of_birth ?? '',
		language: m?.language ?? '',
		license_number: m?.license_number ?? '',
		photo_public_consent: m?.photo_public_consent ?? false,
		club_staff: m?.club_staff ?? false
	};
}

/** Posted form -> API body (empty optional strings become null; the checkbox becomes a boolean). */
export function formToBody(form: FormData): Record<string, unknown> {
	const s = (k: string) => String(form.get(k) ?? '').trim();
	const opt = (k: string) => s(k) || null;
	return {
		first_name: s('first_name'),
		last_name: s('last_name'),
		email: s('email'),
		phone: opt('phone'),
		emergency_phone: opt('emergency_phone'),
		date_of_birth: opt('date_of_birth'),
		language: s('language'),
		license_number: opt('license_number'),
		photo_public_consent: form.get('photo_public_consent') === 'on',
		club_staff: form.get('club_staff') === 'on'
	};
}

export function valuesFromForm(form: FormData): FormValues {
	const s = (k: string) => String(form.get(k) ?? '');
	return {
		first_name: s('first_name'),
		last_name: s('last_name'),
		email: s('email'),
		phone: s('phone'),
		emergency_phone: s('emergency_phone'),
		date_of_birth: s('date_of_birth'),
		language: s('language'),
		license_number: s('license_number'),
		photo_public_consent: form.get('photo_public_consent') === 'on',
		club_staff: form.get('club_staff') === 'on'
	};
}
