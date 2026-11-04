import { api } from '#lib/management-api.ts';
import { loadPools, loadSeasons } from '#lib/teams/server.ts';
import type { NumberStatus } from '#lib/teams/status.ts';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export type Holder = { member_id: string; first_name: string; last_name: string; date_of_birth: string | null; team_id: string; team_name: string };
export type PoolOverview = {
	pool: { id: string; name: string; min_number: number; max_number: number; min_age_gap_years: number } | null;
	teams: { id: string; name: string }[];
	numbers: { number: number; status: Exclude<NumberStatus, 'yours'>; holders: Holder[]; reservation: { id: string; note: string; reserved_by_name?: string | null; can_release?: boolean } | null }[];
};

export const load: PageServerLoad = async ({ url, fetch }) => {
	const season = url.searchParams.get('season') ?? '';
	const pool = url.searchParams.get('pool') ?? '';
	const [overview, seasons, pools] = await Promise.all([api<PoolOverview[]>('GET', '/teams/numbers/overview', { query: { season, pool }, f: fetch }), loadSeasons(fetch), loadPools(fetch)]);
	return { filters: { season, pool }, overview: overview.data ?? [], seasons, pools, error: overview.ok ? null : overview.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { numAction: action, numError: res.fields.number || res.message || 'failed' });

export const actions: Actions = {
	reserve: async ({ request, fetch }) => {
		const form = await request.formData();
		const pool = String(form.get('pool') ?? '');
		const body = { number: Number(form.get('number') || 0), note: String(form.get('note') ?? '').trim() };
		const res = await api('POST', `/number-pools/${pool}/reservations`, { body, f: fetch });
		return res.ok ? { done: 'reserve' } : failed('reserve', res);
	},
	release: async ({ request, fetch }) => {
		const form = await request.formData();
		const pool = String(form.get('pool') ?? '');
		const res = await api('DELETE', `/number-pools/${pool}/reservations/${String(form.get('rid') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'release' } : failed('release', res);
	}
};
