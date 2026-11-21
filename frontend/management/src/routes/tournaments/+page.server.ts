import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { TournamentList } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

/** GET /public/tournaments: no login. The platform host, unknown clubs and clubs without the tournaments feature are a plain 404. */
export const load: PageServerLoad = async ({ fetch }) => {
	const res = await api<TournamentList>('GET', '/public/tournaments', { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { list: res.data };
};
