import { redirect } from '@sveltejs/kit';
import { api, apiPage } from '#lib/management-api.ts';
import type { EventRow } from '#lib/events/types.ts';
import type { Overview } from '#lib/overview.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, fetch, parent }) => {
	if (locals.mode === 'platform') {
		// One club: no picker needed, go straight to its host (never to ourselves).
		const only = locals.myClubs.length === 1 ? locals.myClubs[0] : null;
		if (only && new URL(only.url).hostname !== url.hostname) redirect(303, `/go/${only.slug}`);
		return { overview: null as Overview | null, noTypeCount: null as number | null, upcoming: [] as EventRow[] };
	}
	// A failure here (no permission, backend hiccup) just hides the part that needed it.
	const { me } = (await parent()) as { me?: { can_manage_members: boolean } };
	// The next events everyone in the app may see (the calendar API returns all of them), soonest first.
	const today = new Date();
	const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	const horizon = new Date(today.getTime() + 28 * 86_400_000);
	const [overview, noType, events] = await Promise.all([
		api<Overview>('GET', '/overview', { f: fetch }),
		// People holding no role (member, staff or guardian) in the current season: an issue for member managers.
		me?.can_manage_members ? apiPage<unknown>('/members', { role: 'none', limit: 1 }, fetch) : Promise.resolve(null),
		api<EventRow[]>('GET', '/events', { query: { from: iso(today), to: iso(horizon) }, f: fetch })
	]);
	const now = Date.now();
	const upcoming = (events.ok ? (events.data ?? []) : []).filter((e) => !e.cancelled && new Date(e.end ?? e.start).getTime() >= now).slice(0, 8);
	return { upcoming, overview: overview.ok ? (overview.data ?? null) : null, noTypeCount: noType?.ok && noType.data ? noType.data.count : null };
};
