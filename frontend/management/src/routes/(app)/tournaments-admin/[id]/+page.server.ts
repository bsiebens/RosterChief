import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { TBASE_PATH } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

/** The settings page is for managers; operators land on the teams. */
export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	const { me } = await parent();
	if (!me.can_manage_tournaments) redirect(303, `${TBASE_PATH}/${params.id}/teams`);
	const teams = await api<{ id: string; name: string }[]>('GET', '/members/teams', { f: fetch });
	return { teams: teams.data ?? [] };
};
