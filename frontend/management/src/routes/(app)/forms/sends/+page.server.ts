import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, type PagedList, type SendListItem } from '#lib/forms.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const TABS = ['open', 'scheduled', 'closed', 'all'] as const;
type Tab = (typeof TABS)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	const requested = url.searchParams.get('tab') as Tab;
	const tab: Tab = TABS.includes(requested) ? requested : 'open';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	// The API filters on open | closed | all; "scheduled" is the not-yet-started subset, so it is fetched with "all" and narrowed here.
	const status = tab === 'open' ? 'open' : tab === 'closed' ? 'closed' : 'all';
	const narrowed = tab === 'scheduled';
	const res = await api<PagedList<SendListItem> | SendListItem[]>('GET', '/form-sends', { query: { status, limit: narrowed ? 200 : PAGE_SIZE, offset: narrowed ? undefined : offset || undefined }, f: fetch });
	const list = asList(res.data);
	const rows = narrowed ? list.results.filter((r) => r.status === 'scheduled') : list.results;
	return { filters: { tab, offset: narrowed ? 0 : offset }, rows, count: narrowed ? rows.length : list.count, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};
