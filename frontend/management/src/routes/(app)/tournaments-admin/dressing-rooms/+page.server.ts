import { api } from '#lib/management-api.ts';
import { requireManage } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export type Room = { id: string; name: string; capacity: number | null; notes: string };

export const load: PageServerLoad = async ({ fetch, parent }) => {
	await requireManage(parent);
	const res = await api<{ results: Room[] }>('GET', '/dressing-rooms', { f: fetch });
	return { rooms: res.data?.results ?? [], error: res.ok ? null : res.status };
};
