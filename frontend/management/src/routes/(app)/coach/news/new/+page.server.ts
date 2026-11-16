import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { TeamOption } from '#lib/news.ts';
import type { PageServerLoad } from './$types';

/** The coach app's "new post" page: anyone who may add news (saved as a draft); others go back to the coach home. */
export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me?.can_add_news) redirect(303, '/coach');
	const teams = await api<TeamOption[]>('GET', '/news/team-options', { f: fetch });
	return { teams: teams.data ?? [], canPublish: !!me.can_publish_news };
};
