import { error, fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { AttachmentList, AttendanceList, EventDetail, GameSheet, Lineup, TaskList } from '#lib/events/types.ts';
import type { Actions, PageServerLoad, RequestEvent } from './$types';

const TABS = ['overview', 'attendance', 'lineup', 'sheet', 'tasks', 'files'] as const;
type Tab = (typeof TABS)[number];

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const id = params.id;
	const [event, attendance, lineup, tasks, attachments, sheet] = await Promise.all([
		api<EventDetail>('GET', `/events/${id}`, { f: fetch }),
		api<AttendanceList>('GET', `/events/${id}/attendance`, { f: fetch }),
		api<Lineup>('GET', `/events/${id}/lineup`, { f: fetch }),
		api<TaskList>('GET', `/events/${id}/tasks`, { f: fetch }),
		api<AttachmentList>('GET', `/events/${id}/attachments`, { f: fetch }),
		api<GameSheet>('GET', `/events/${id}/game-sheet`, { f: fetch })
	]);
	if (!event.ok) error(event.status === 404 ? 404 : event.status || 500, event.message);
	const requested = url.searchParams.get('tab') as Tab;
	let tab: Tab = TABS.includes(requested) ? requested : 'overview';
	if (tab === 'lineup' && !event.data!.lineup.available) tab = 'overview';
	if (tab === 'sheet' && !sheet.data) tab = 'overview';
	return {
		event: event.data!,
		tab,
		attendance: attendance.data ?? null,
		lineup: lineup.data ?? null,
		tasks: tasks.data ?? null,
		attachments: attachments.data ?? null,
		sheet: sheet.data ?? null
	};
};

const setCancelled = (cancelled: boolean) => async ({ request, params, fetch }: RequestEvent) => {
	const form = await request.formData();
	const notify = form.get('notify') === 'on';
	const res = await api('POST', `/events/${params.id}/${cancelled ? 'cancel' : 'uncancel'}`, { query: { notify: notify ? 'true' : 'false' }, f: fetch });
	if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { cancelError: res.fields.__all__ || res.message || 'failed' });
	return { done: cancelled ? 'cancel' : 'uncancel' };
};

const updateGame = async ({ params, fetch }: RequestEvent) => {
	const res = await api('POST', `/events/${params.id}/update-game`, { f: fetch });
	if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { updateError: res.fields.event || res.message || 'failed' });
	return { updated: true };
};

export const actions: Actions = {
	updateGame,
	cancel: setCancelled(true),
	uncancel: setCancelled(false)
};
