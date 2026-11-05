import { api } from '#lib/management-api.ts';
import { AGENDA_DAYS, addDays, isoDate, parseIsoDate, weekStart, type EventOptions, type EventRow } from '#lib/events/types.ts';
import type { TeamRow } from '#lib/teams/server.ts';
import type { PageServerLoad } from './$types';

export type CalendarView = 'month' | 'week' | 'agenda';
const VIEWS: CalendarView[] = ['month', 'week', 'agenda'];
/** The API refuses ranges longer than this (api/routers/events.py MAX_RANGE_DAYS). */
const MAX_RANGE_DAYS = 100;
/** The first and last day the view shows (both inclusive). */
function range(view: CalendarView, anchor: Date): { from: Date; to: Date } {
	if (view === 'week') {
		const from = weekStart(anchor);
		return { from, to: addDays(from, 6) };
	}
	if (view === 'agenda') return { from: anchor, to: addDays(anchor, AGENDA_DAYS - 1) };
	// Month: the 6-week grid around the month, Monday first.
	const from = weekStart(new Date(anchor.getFullYear(), anchor.getMonth(), 1));
	return { from, to: addDays(from, 41) };
}

export const load: PageServerLoad = async ({ url, fetch }) => {
	const p = url.searchParams;
	const view: CalendarView = VIEWS.includes(p.get('view') as CalendarView) ? (p.get('view') as CalendarView) : 'month';
	const anchor = parseIsoDate(p.get('date')) ?? new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
	const { from, to: rawTo } = range(view, anchor);
	const to = (rawTo.getTime() - from.getTime()) / 86_400_000 > MAX_RANGE_DAYS ? addDays(from, MAX_RANGE_DAYS) : rawTo;
	const filters = { team: p.get('team') ?? '', location: p.get('location') ?? '', opponent: p.get('opponent') ?? '', kind: p.get('kind') ?? '', mine: p.get('mine') === '1' };

	const [events, options, teams] = await Promise.all([
		api<EventRow[]>('GET', '/events', { query: { from: isoDate(from), to: isoDate(to), team: filters.team, location: filters.location, opponent: filters.opponent, kind: filters.kind, mine: filters.mine ? 'true' : '' }, f: fetch }),
		api<EventOptions>('GET', '/events/options', { f: fetch }),
		// Everyone sees every event, so the team filter lists every team (the options only hold the teams you may schedule for).
		api<TeamRow[]>('GET', '/teams', { f: fetch })
	]);

	return {
		view,
		date: isoDate(anchor),
		from: isoDate(from),
		to: isoDate(to),
		filters,
		events: events.data ?? [],
		error: events.ok ? null : { status: events.status, message: events.message ?? '' },
		options: options.data ?? null,
		teams: (teams.data ?? []).map((t) => ({ id: t.id, name: t.name }))
	};
};
