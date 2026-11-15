import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { NewsDetail, TeamOption } from '#lib/news.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => {
	const [item, teams] = await Promise.all([api<NewsDetail>('GET', `/news/${params.id}`, { f: fetch }), api<TeamOption[]>('GET', '/news/team-options', { f: fetch })]);
	if (!item.ok || !item.data) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data, teams: teams.data ?? [] };
};
