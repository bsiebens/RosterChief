import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { NEEDS, RANGES, TABS, type GamesOut, type Summary, type Tab } from '#lib/referees.ts';
import type { PageServerLoad } from './$types';

/** The referee management page: games that need referees / officials, their payments and the workload. */
export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = (await parent()) as { me: { features: string[]; can_manage_referees: boolean } };
	if (!me.can_manage_referees) error(403);
	if (!me.features.includes('referee_officials')) error(404);
	const p = url.searchParams;
	const tab: Tab = TABS.includes(p.get('tab') as Tab) ? (p.get('tab') as Tab) : 'games';
	const range = RANGES.includes(p.get('range') as (typeof RANGES)[number]) ? (p.get('range') as string) : 'two_weeks';
	const need = NEEDS.includes(p.get('need') as (typeof NEEDS)[number]) ? (p.get('need') as string) : 'all';

	const [summary, games] = await Promise.all([
		api<Summary>('GET', '/referee-management/summary', { f: fetch }),
		tab === 'workload' ? Promise.resolve(null) : api<GamesOut>('GET', '/referee-management/games', { query: tab === 'payments' ? { scope: 'season' } : { range, need }, f: fetch })
	]);
	return {
		tab,
		filters: { range, need },
		summary: summary.data ?? null,
		list: games?.data ?? null,
		error: summary.ok && (!games || games.ok) ? null : (games && !games.ok ? games.status : summary.status)
	};
};
