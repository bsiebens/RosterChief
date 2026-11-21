import { loadOr } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export type TaskClaim = { id: string; member: { id: string; name: string }; label: string; claimed_at: string };
export type TaskDoc = {
	id: string;
	title: string;
	description: string;
	starts_at: string;
	ends_at: string;
	location_label: string;
	needed_quantity: number;
	claimed_count: number;
	open_slots: number;
	is_full: boolean;
	claims: TaskClaim[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<{ results: TaskDoc[] }>(fetch, `/tournaments/${params.id}/tasks`);
	return { tasks: data?.results ?? [], error: data ? null : status };
};
