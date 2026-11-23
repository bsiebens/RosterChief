import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { TournamentDetail } from '#lib/tournaments-public.ts';
import type { LayoutServerLoad } from './$types';

/** GET /public/tournaments/{slug}: a tournament's own pages work for every status. Unknown slug or feature off: a plain 404. */
export const load: LayoutServerLoad = async ({ params, fetch }) => {
	const res = await api<TournamentDetail>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}`, { f: fetch });
	if (res.status === 429) error(429, 'rate_limited');
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	if (String(res.data.year) !== params.year) error(404); // /tournaments/<year>/<slug>: the year has to be the tournament's
	return { tournament: res.data };
};
