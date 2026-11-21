import { loadOr } from '#lib/tournaments-server.ts';
import type { TaskDoc } from '../+page.server.ts';
import type { PageServerLoad } from './$types';

export type PrintTask = TaskDoc & { timeline_left_pct: number; timeline_width_pct: number };
export type PrintData = { timeline_start: string; timeline_end: string; locations: { location_label: string; tasks: PrintTask[] }[] };

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<PrintData>(fetch, `/tournaments/${params.id}/tasks/print`);
	return { print: data, error: data ? null : status };
};
