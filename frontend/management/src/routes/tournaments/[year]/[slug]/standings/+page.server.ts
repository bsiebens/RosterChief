import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PublicStandings } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicStandings>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}/standings`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { standings: res.data };
};
