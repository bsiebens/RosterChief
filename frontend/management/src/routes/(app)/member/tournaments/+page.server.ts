import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PortalShift, PortalShiftTournament } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

/** GET /portal/tournaments (tournaments with volunteer shifts) and each one's shifts. The feature off is a 404. */
export const load: PageServerLoad = async ({ fetch }) => {
	const list = await api<{ results: PortalShiftTournament[] }>('GET', '/portal/tournaments', { f: fetch });
	if (!list.ok || !list.data) error(list.status === 403 || list.status === 404 ? list.status : 502, list.message);
	const tournaments = await Promise.all(
		list.data.results.map(async (tn) => {
			const res = await api<{ results: PortalShift[] }>('GET', `/portal/tournaments/${encodeURIComponent(tn.slug)}/tasks`, { f: fetch });
			return { ...tn, shifts: res.ok ? (res.data?.results ?? []) : [] };
		})
	);
	return { tournaments };
};
