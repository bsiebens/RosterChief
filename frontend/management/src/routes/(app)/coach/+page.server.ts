import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { NewsDetail, NewsList } from '#lib/news.ts';
import type { CoachHome } from '#lib/portal.ts';
import { intOrNull, strOrNull, teamCall, text, type Position, type TeamDetail } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url }) => {
	const tab = url.searchParams.get('tab') || 'home';
	const result = await api<CoachHome>('GET', '/portal/coach', { f: fetch });
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	const coach = result.data;
	// A link to one news item (a notification): a published one opens in the reading drawer, anything else (a draft or an item in review) in the mobile editor.
	const article = url.searchParams.get('article');
	if (tab === 'news' && article && !coach.news.some((item) => item.id === article)) {
		const found = await api<NewsDetail>('GET', `/news/${article}`, { f: fetch });
		if (found.ok && found.data) redirect(303, `/coach/news/${article}`);
	}
	// Only the tab being shown needs its extra data: the rosters of the coach's teams for Squad, the news list (drafts included) for News.
	const [rosters, news, positions] = await Promise.all([
		tab === 'teams' ? Promise.all(coach.teams.map((team) => api<TeamDetail>('GET', `/teams/${team.id}`, { f: fetch }))) : Promise.resolve([]),
		tab === 'news' ? api<NewsList>('GET', '/news', { query: { limit: 30, mine: url.searchParams.get('mine') === '1' }, f: fetch }) : Promise.resolve(null),
		tab === 'teams' ? api<Position[]>('GET', '/team-positions', { f: fetch }) : Promise.resolve(null)
	]);
	return {
		coach,
		rosters: rosters.flatMap((roster) => (roster.ok && roster.data ? [roster.data] : [])),
		newsItems: news?.ok && news.data ? news.data.results : null,
		positions: positions?.ok && positions.data ? positions.data : []
	};
};

// The squad editor: the same team API as the manage app (the API decides who may change which team, and refuses a coach their own staff entry).
const flag = (form: FormData, key: string) => form.get(key) === 'on';
const rosterBody = (form: FormData) => ({ position_id: strOrNull(form, 'position_id'), jersey_number: intOrNull(form, 'jersey_number'), is_captain: flag(form, 'is_captain'), is_alternate_captain: flag(form, 'is_alternate_captain') });

export const actions: Actions = {
	addPlayer: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('addPlayer', 'POST', `/teams/${text(form, 'team_id')}/roster`, fetch, { member_id: text(form, 'member_id'), season_id: strOrNull(form, 'season_id'), ...rosterBody(form) });
		return failure ?? { done: 'addPlayer' };
	},
	editPlayer: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('editPlayer', 'PATCH', `/teams/${text(form, 'team_id')}/roster/${text(form, 'membership_id')}`, fetch, rosterBody(form));
		return failure ?? { done: 'editPlayer' };
	},
	removePlayer: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('removePlayer', 'DELETE', `/teams/${text(form, 'team_id')}/roster/${text(form, 'membership_id')}`, fetch);
		return failure ?? { done: 'removePlayer' };
	},
	addStaff: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('addStaff', 'POST', `/teams/${text(form, 'team_id')}/staff`, fetch, { member_id: text(form, 'member_id'), season_id: strOrNull(form, 'season_id'), position_id: text(form, 'position_id') });
		return failure ?? { done: 'addStaff' };
	},
	editStaff: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('editStaff', 'PATCH', `/teams/${text(form, 'team_id')}/staff/${text(form, 'assignment_id')}`, fetch, { position_id: text(form, 'position_id') });
		return failure ?? { done: 'editStaff' };
	},
	removeStaff: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('removeStaff', 'DELETE', `/teams/${text(form, 'team_id')}/staff/${text(form, 'assignment_id')}`, fetch);
		return failure ?? { done: 'removeStaff' };
	}
};
