import { loadOr } from '#lib/tournaments-server.ts';
import type { TTeamRef } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type RoomAssignment = { id: string; room: { id: string; name: string }; team?: unknown; assigned_from: string | null; assigned_until: string | null; note: string; conflicts: { assignment_id: string; label: string }[] };
export type RoomsData = { teams: { team: TTeamRef; assignments: RoomAssignment[] }[]; rooms: { id: string; name: string; capacity: number | null; notes?: string }[] };

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<RoomsData>(fetch, `/tournaments/${params.id}/dressing-rooms`);
	return { data, error: data ? null : status };
};
