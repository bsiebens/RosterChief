import { error, redirect } from '@sveltejs/kit';
import { api, type Fetch } from '#lib/management-api.ts';
import type { Me } from '#lib/types.ts';

const BASE = '/tournaments-admin';
export const TBASE_PATH = BASE;

/** Any tournaments user (view tier): the feature must be on and the user must manage or operate tournaments. */
export async function requireTournaments(parent: () => Promise<{ me: Me }>): Promise<Me> {
	const { me } = await parent();
	if (!me.features.includes('tournaments') || !(me.can_manage_tournaments || me.can_operate_tournaments)) redirect(303, '/');
	return me;
}

export async function requireManage(parent: () => Promise<{ me: Me }>): Promise<Me> {
	const me = await requireTournaments(parent);
	if (!me.can_manage_tournaments) redirect(303, BASE);
	return me;
}

/** GET for a load function: 404 and 403 become real error pages, any other failure a null result with its status. */
export async function loadOr<T>(f: Fetch, path: string, query?: Record<string, string | undefined>): Promise<{ data: T | null; status: number }> {
	const res = await api<T>('GET', path, { f, query });
	if (res.status === 404) error(404);
	if (res.status === 403) error(403);
	return { data: res.ok ? (res.data ?? null) : null, status: res.ok ? 200 : res.status };
}
