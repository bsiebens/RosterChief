import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { AttendanceList, EventDetail } from '#lib/events/types.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const [event, attendance] = await Promise.all([
		api<EventDetail>('GET', `/events/${params.id}`, { f: fetch }),
		api<AttendanceList>('GET', `/events/${params.id}/attendance`, { f: fetch })
	]);
	if (!event.data || !attendance.data) {
		const failure = event.ok ? attendance : event;
		error(failure.status || 502, failure.message);
	}
	if (!attendance.data.can_manage) error(403);
	return { event: event.data, attendance: attendance.data };
};
