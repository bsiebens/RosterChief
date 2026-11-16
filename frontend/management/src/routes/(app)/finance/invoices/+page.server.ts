import { redirect } from '@sveltejs/kit';
import { badPayload, failed, invoicesOn, parsePayload, STATUS_TABS, TYPES, type InvoiceList, type InvoiceType, type StatusTab } from '#lib/invoices.ts';
import type { ChargeSettings } from '#lib/charges.ts';
import { api } from '#lib/management-api.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!invoicesOn(me)) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const askedStatus = url.searchParams.get('tab') as StatusTab;
	const tab: StatusTab = STATUS_TABS.includes(askedStatus) ? askedStatus : 'all';
	const askedType = url.searchParams.get('type') as InvoiceType;
	const type: InvoiceType | 'all' = TYPES.includes(askedType) ? askedType : 'all';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [list, settings] = await Promise.all([
		api<InvoiceList>('GET', '/finance/invoices', { query: { type, status: tab, q: q || undefined, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		me.can_manage_charges ? api<ChargeSettings>('GET', '/finance/charges/settings', { f: fetch }) : Promise.resolve(null)
	]);
	return {
		rows: list.data?.results ?? [],
		count: list.data?.count ?? 0,
		types: list.data?.types ?? [],
		summary: list.data?.summary ?? {},
		settings: settings?.ok ? (settings.data ?? null) : null,
		filters: { q, tab, type, offset },
		pageSize: PAGE_SIZE,
		error: list.ok ? null : list.status
	};
};

export const actions: Actions = {
	/** The reminder settings of the charge invoices (overdue reminders). */
	saveSettings: async ({ request, fetch }) => {
		const body = parsePayload<Partial<ChargeSettings>>(await request.formData());
		if (!body) return badPayload('saveSettings');
		const res = await api<ChargeSettings>('PUT', '/finance/charges/settings', { body, f: fetch });
		return res.ok ? { done: 'saveSettings' } : failed('saveSettings', res);
	}
};
