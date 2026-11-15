import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { NewsList } from '#lib/news.ts';
import type { CoachHome } from '#lib/portal.ts';
import type { TeamDetail } from '#lib/teams/server.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const tab = url.searchParams.get('tab') || 'home';
	const result = await api<CoachHome>('GET', '/portal/coach', { f: fetch });
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	const coach = result.data;
	// Only the tab being shown needs its extra data: the rosters of the coach's teams for Squad, the news list (drafts included) for News.
	const [rosters, news] = await Promise.all([
		tab === 'teams' ? Promise.all(coach.teams.map((team) => api<TeamDetail>('GET', `/teams/${team.id}`, { f: fetch }))) : Promise.resolve([]),
		tab === 'news' ? api<NewsList>('GET', '/news', { query: { limit: 30 }, f: fetch }) : Promise.resolve(null)
	]);
	return {
		coach,
		rosters: rosters.flatMap((roster) => (roster.ok && roster.data ? [roster.data] : [])),
		newsItems: news?.ok && news.data ? news.data.results : null
	};
};
