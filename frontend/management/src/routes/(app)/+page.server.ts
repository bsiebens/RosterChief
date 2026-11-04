import { redirect } from '@sveltejs/kit';
import { api, apiPage } from '#lib/management-api.ts';
import type { Overview } from '#lib/overview.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, fetch, parent }) => {
	if (locals.mode === 'platform') {
		// One club: no picker needed, go straight to its host (never to ourselves).
		const only = locals.myClubs.length === 1 ? locals.myClubs[0] : null;
		if (only && new URL(only.url).hostname !== url.hostname) redirect(303, `/go/${only.slug}`);
		return { overview: null as Overview | null, noTypeCount: null as number | null };
	}
	// A failure here (no permission, backend hiccup) just hides the part that needed it.
	const { me } = (await parent()) as { me?: { can_manage_members: boolean } };
	const [overview, noType] = await Promise.all([
		api<Overview>('GET', '/overview', { f: fetch }),
		// People holding no role (member, staff or guardian) in the current season: an issue for member managers.
		me?.can_manage_members ? apiPage<unknown>('/members', { role: 'none', limit: 1 }, fetch) : Promise.resolve(null)
	]);
	return { overview: overview.ok ? (overview.data ?? null) : null, noTypeCount: noType?.ok && noType.data ? noType.data.count : null };
};
