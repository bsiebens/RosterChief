import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { blankState } from '#lib/events/form-types.ts';
import { EVENT_KINDS, eventHref, type EventDetail, type EventIn, type EventOptions } from '#lib/events/types.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Add an event. Prefill from the query: `date` (YYYY-MM-DD, starts at 19:00) or `start` (YYYY-MM-DDTHH:MM), `kind`,
 * `team`, `group`. Users who may not schedule anything are sent back to the calendar.
 */
export const load: PageServerLoad = async ({ url, fetch }) => {
	const res = await api<EventOptions>('GET', '/events/options', { f: fetch });
	if (!res.ok || !res.data?.can_create) redirect(303, url.searchParams.get('view') === 'coach' ? '/coach' : '/calendar');
	const options = res.data;
	const q = (k: string) => url.searchParams.get(k) ?? '';
	const date = /^\d{4}-\d{2}-\d{2}$/.test(q('date')) ? q('date') : '';
	const start = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(q('start')) ? q('start') : date ? `${date}T19:00` : '';
	const kind = (EVENT_KINDS as readonly string[]).includes(q('kind')) ? q('kind') : 'training';
	const team = options.teams.find((x) => x.id === q('team'));
	const group = options.groups.find((x) => x.id === q('group'));
	// One team and nothing else on offer: preselect it.
	const onlyTeam = options.teams.length === 1 && !options.groups.length && !options.can_club_wide ? options.teams[0] : undefined;
	const pickTeam = team ?? (group ? undefined : onlyTeam);
	return {
		options,
		initial: blankState({
			kind,
			start,
			// New events start at the club's own ground; clear it for a different one.
			location_id: options.locations.find((l) => l.is_home)?.id ?? '',
			team_ids: pickTeam ? [pickTeam.id] : [],
			group_ids: group ? [group.id] : []
		})
	};
};

export const actions: Actions = {
	save: async ({ request, url, fetch }) => {
		const form = await request.formData();
		let body: EventIn;
		try {
			body = JSON.parse(String(form.get('payload') ?? ''));
		} catch {
			return fail(400, { fields: {} as Record<string, string>, message: 'Invalid data.', status: 400 });
		}
		const res = await api<EventDetail>('POST', '/events', { body, f: fetch });
		if (!res.ok) {
			const status = res.status >= 400 && res.status < 600 ? res.status : 500;
			return fail(status, { fields: res.fields, message: res.message ?? '', status: res.status });
		}
		redirect(303, `${eventHref(res.data!.id)}?created=1${url.searchParams.get('view') === 'coach' ? '&view=coach' : ''}`);
	}
};
