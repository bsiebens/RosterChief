import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { REGISTRATION_TABS, type RegistrationList, type RegistrationTab } from '#lib/registrations.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_finance) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const asked = url.searchParams.get('status') as RegistrationTab;
	const status: RegistrationTab = REGISTRATION_TABS.includes(asked) ? asked : 'awaiting';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<RegistrationList>('GET', '/registrations', { query: { status, q, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch });
	return { filters: { q, status, offset }, rows: res.data?.results ?? [], count: res.data?.count ?? 0, awaiting: res.data?.awaiting_count ?? 0, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};
