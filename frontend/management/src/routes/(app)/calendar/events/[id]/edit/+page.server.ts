import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { diffDays, isoToLocalInput, SCOPES, valuesToState } from '#lib/events/form-types.ts';
import { eventHref, type EventDetail, type EventForm, type EventOptions, type EventPatch } from '#lib/events/types.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Edit an event (or, through `scope`, its series). Starting values from GET /events/{id}/form; member names, the
 * series and its description from GET /events/{id}. People who may not change it go to the event page instead.
 */
export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const id = params.id;
	const [form, detail, options] = await Promise.all([
		api<EventForm>('GET', `/events/${id}/form`, { f: fetch }),
		api<EventDetail>('GET', `/events/${id}`, { f: fetch }),
		api<EventOptions>('GET', '/events/options', { f: fetch })
	]);
	if (!form.ok || !detail.ok) error(form.status === 404 || detail.status === 404 ? 404 : form.status || detail.status || 500);
	const view = url.searchParams.get('view') === 'coach' ? '?view=coach' : '';
	if (!form.data!.can_edit || !options.ok) redirect(303, `${eventHref(id)}${view}`);
	const f = form.data!;
	const event = detail.data!;
	const recurrence = f.series?.recurrence ?? event.series?.recurrence ?? null;
	const initial = valuesToState(f.event, { invited: event.invited_members, excluded: event.excluded_members }, event.series_id ? recurrence : null);
	const seriesStart = f.series?.start ?? event.series?.dtstart ?? null;
	return {
		id,
		event,
		options: options.data!,
		initial,
		series: event.series_id
			? {
					description: f.series_description || event.series?.description || '',
					// The series' times are those of its first occurrence: the day offset from this one.
					shiftDays: seriesStart && f.event.start ? diffDays(isoToLocalInput(f.event.start), isoToLocalInput(seriesStart)) : 0,
					detached: event.detached
				}
			: null
	};
};

const scopeOf = (form: FormData) => {
	const s = String(form.get('scope') ?? 'this');
	return (SCOPES as readonly string[]).includes(s) ? s : 'this';
};
const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);

export const actions: Actions = {
	save: async ({ request, params, url, fetch }) => {
		const coach = url.searchParams.get('view') === 'coach';
		const form = await request.formData();
		let body: EventPatch;
		try {
			body = JSON.parse(String(form.get('payload') ?? ''));
		} catch {
			return fail(400, { fields: {} as Record<string, string>, message: 'Invalid data.', status: 400 });
		}
		const res = await api<EventDetail>('PATCH', `/events/${params.id}`, { body, query: { scope: scopeOf(form) }, f: fetch });
		if (!res.ok) {
			if (res.status === 404) error(404);
			return fail(httpStatus(res.status), { fields: res.fields, message: res.message ?? '', status: res.status });
		}
		// 204: the new recurrence no longer produces this event and nothing of the series is left to show.
		if (res.status === 204 || !res.data) redirect(303, coach ? '/coach?tab=calendar' : '/calendar');
		redirect(303, `${eventHref(res.data.id)}?saved=1${coach ? '&view=coach' : ''}`);
	},
	delete: async ({ request, params, url, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/events/${params.id}`, { query: { scope: scopeOf(form) }, f: fetch });
		if (!res.ok) return fail(httpStatus(res.status), { deleteError: res.fields.scope || res.message || 'failed', deleteStatus: res.status });
		redirect(303, url.searchParams.get('view') === 'coach' ? '/coach?tab=calendar' : '/calendar');
	}
};
