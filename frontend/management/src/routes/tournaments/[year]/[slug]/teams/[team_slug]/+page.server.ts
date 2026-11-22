import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PublicTeam } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicTeam>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}/teams/${encodeURIComponent(params.team_slug)}`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { team: res.data };
};
