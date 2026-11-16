import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, chargesOn, failed, parsePayload, TABS, type ChargeList, type ChargeSettings, type ChargeTab } from '#lib/charges.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!chargesOn(me)) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const asked = url.searchParams.get('tab') as ChargeTab;
	const tab: ChargeTab = TABS.includes(asked) ? asked : 'all';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [list, settings] = await Promise.all([
		api<ChargeList>('GET', '/finance/charges', { query: { status: tab, q: q || undefined, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		api<ChargeSettings>('GET', '/finance/charges/settings', { f: fetch })
	]);
	return {
		rows: list.data?.results ?? [],
		count: list.data?.count ?? 0,
		summary: list.data?.summary ?? { drafts: 0, open: 0, overdue: 0, paid: 0, cancelled: 0, open_amount: '0.00', overdue_amount: '0.00' },
		settings: settings.ok ? (settings.data ?? null) : null,
		filters: { q, tab, offset },
		pageSize: PAGE_SIZE,
		error: list.ok ? null : list.status
	};
};

export const actions: Actions = {
	saveSettings: async ({ request, fetch }) => {
		const body = parsePayload<Partial<ChargeSettings>>(await request.formData());
		if (!body) return badPayload('saveSettings');
		const res = await api<ChargeSettings>('PUT', '/finance/charges/settings', { body, f: fetch });
		return res.ok ? { done: 'saveSettings' } : failed('saveSettings', res);
	}
};
