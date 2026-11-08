import { redirect } from '@sveltejs/kit';
import { api, apiPage } from '#lib/management-api.ts';
import type { EventRow } from '#lib/events/types.ts';
import type { NewsListItem } from '#lib/news.ts';
import type { Overview } from '#lib/overview.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, fetch, parent }) => {
	if (locals.mode === 'platform') {
		// One club: no picker needed, go straight to its host (never to ourselves).
		const only = locals.myClubs.length === 1 ? locals.myClubs[0] : null;
		if (only && new URL(only.url).hostname !== url.hostname) redirect(303, `/go/${only.slug}`);
		return { overview: null as Overview | null, noTypeCount: null as number | null, upcoming: [] as EventRow[], news: [] as NewsListItem[], newsPending: 0 };
	}
	// A failure here (no permission, backend hiccup) just hides the part that needed it.
	const { me } = (await parent()) as { me?: { can_manage_members: boolean; can_add_news?: boolean; can_publish_news?: boolean } };
	// The next events everyone in the app may see (the calendar API returns all of them), soonest first.
	const today = new Date();
	const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	const horizon = new Date(today.getTime() + 60 * 86_400_000);
	const [overview, noType, events, news, pending] = await Promise.all([
		api<Overview>('GET', '/overview', { f: fetch }),
		// People holding no role (member, staff or guardian) in the current season: an issue for member managers.
		me?.can_manage_members ? apiPage<unknown>('/members', { role: 'none', limit: 1 }, fetch) : Promise.resolve(null),
		api<EventRow[]>('GET', '/events', { query: { from: iso(today), to: iso(horizon) }, f: fetch }),
		// The latest live news the user may see (publishers: all; others: their own), and for publishers the review queue size.
		me?.can_add_news ? api<{ results: NewsListItem[] }>('GET', '/news', { query: { live: true, limit: 5 }, f: fetch }) : Promise.resolve(null),
		me?.can_publish_news ? api<{ count: number }>('GET', '/news', { query: { status: 'pending_review', limit: 1 }, f: fetch }) : Promise.resolve(null)
	]);
	const now = Date.now();
	const upcoming = (events.ok ? (events.data ?? []) : []).filter((e) => !e.cancelled && new Date(e.end ?? e.start).getTime() >= now).slice(0, 12);
	return { upcoming, news: news?.ok ? (news.data?.results ?? []) : [], newsPending: pending?.ok ? (pending.data?.count ?? 0) : 0, overview: overview.ok ? (overview.data ?? null) : null, noTypeCount: noType?.ok && noType.data ? noType.data.count : null };
};
