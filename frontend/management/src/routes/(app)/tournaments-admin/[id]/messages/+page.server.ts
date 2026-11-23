import { loadOr } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export type TeamMessage = { id: string; sender: 'organiser' | 'team'; author: string; body: string; created: string; broadcast: boolean };
export type Thread = { registration_id: string; team: string; contact: string; unread: number; last_message: TeamMessage | null };
export type ThreadsDoc = { threads: Thread[]; unread_total: number; broadcasts: TeamMessage[] };

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<ThreadsDoc>(fetch, `/tournaments/${params.id}/messages`);
	return { threads: data, error: data ? null : status };
};
