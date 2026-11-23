import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PublicGame } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

/** GET /public/tournaments/{slug}/matches/{id}: one game with its periods and their timelines. */
export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicGame>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}/matches/${encodeURIComponent(params.match)}`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { game: res.data };
};
