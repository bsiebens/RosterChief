import { error, fail } from '@sveltejs/kit';
import { SCOPES, STRENGTHS, type Scope, type TeamAnalytics } from '#lib/analytics.ts';
import { api } from '#lib/management-api.ts';
import { loadSeasons, type TeamDetail } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * A team's game analytics. The filters live in the URL (`?season=&scope=&friendlies=0&competition=`). When the club has
 * no analytics (the team says `analytics_available: false`, or the analytics call answers 404) the page shows a
 * friendly note with the way back instead of the cards.
 */
export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const q = (k: string) => url.searchParams.get(k) ?? '';
	const season = q('season');
	const scope: Scope = (SCOPES as readonly string[]).includes(q('scope')) ? (q('scope') as Scope) : 'all';
	const friendlies = q('friendlies') !== '0';
	const competition = q('competition');
	const [team, seasons] = await Promise.all([api<TeamDetail & { analytics_available?: boolean }>('GET', `/teams/${params.id}`, { query: { season }, f: fetch }), loadSeasons(fetch)]);
	if (!team.ok) error(team.status === 404 ? 404 : team.status || 500);
	const base = { team: { id: team.data!.id, name: team.data!.name, season_id: team.data!.season_id }, seasons, season, scope, friendlies, competition };
	if (!team.data!.analytics_available) return { ...base, unavailable: true as const, analytics: null, problem: null };

	const res = await api<TeamAnalytics>('GET', `/teams/${params.id}/analytics`, { query: { season, scope, friendlies: friendlies ? undefined : 'false', competition }, f: fetch });
	if (res.status === 404) return { ...base, unavailable: true as const, analytics: null, problem: null };
	// 422: no current season (or an unknown one); anything else: the API failed. Both leave the cards out with a note.
	if (!res.ok) return { ...base, unavailable: false as const, analytics: null, problem: res.status === 422 ? 'season' : 'failed' };
	return { ...base, unavailable: false as const, analytics: res.data!, problem: null };
};

export const actions: Actions = {
	/** Count a flagged goal with another strength, or keep the recorded one (both clear the warning). */
	strength: async ({ request, fetch }) => {
		const form = await request.formData();
		const event = String(form.get('event') ?? '');
		const goal = String(form.get('goal') ?? '');
		const strength = String(form.get('strength') ?? '');
		if (!event || !goal || !(STRENGTHS as readonly string[]).includes(strength)) return fail(422, { strengthError: 'invalid', key: `${event}/${goal}` });
		const res = await api('PUT', `/events/${event}/strength-overrides/${encodeURIComponent(goal)}`, { body: { strength }, f: fetch });
		if (!res.ok) return fail(res.status || 500, { strengthError: res.status === 403 ? 'forbidden' : res.message || 'failed', key: `${event}/${goal}` });
		return { strengthDone: `${event}/${goal}` };
	}
};
