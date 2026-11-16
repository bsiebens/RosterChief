import { fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import type { Actions, PageServerLoad } from './$types';

export type Pool = { id: string; name: string; min_number: number; max_number: number; min_age_gap_years: number; team_ids: string[]; teams: { id: string; name: string }[]; reservation_count: number };
export type Reservation = { id: string; number: number; note: string; reserved_by_name?: string | null; can_release?: boolean };

export const load: PageServerLoad = async ({ fetch, parent }) => {
	await requireSettings(parent);
	const [pools, teams] = await Promise.all([api<Pool[]>('GET', '/number-pools', { f: fetch }), api<{ id: string; name: string }[]>('GET', '/members/teams', { f: fetch })]);
	return { pools: pools.data ?? [], teams: teams.data ?? [], error: pools.ok ? null : pools.status };
};

type Res = { status: number; message?: string; fields: Record<string, string> };
const failed = (action: string, res: Res) => fail(res.status >= 400 && res.status < 600 ? res.status : 500, { poolAction: action, poolError: res.message || 'failed', poolFields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const body = {
			name: String(form.get('name') ?? '').trim(),
			min_number: Number(form.get('min_number') || 0),
			max_number: Number(form.get('max_number') || 0),
			min_age_gap_years: Number(form.get('min_age_gap_years') || 0),
			team_ids: form.getAll('team_ids').map(String)
		};
		const res = id ? await api('PATCH', `/number-pools/${id}`, { body, f: fetch }) : await api('POST', '/number-pools', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/number-pools/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	},
	reserve: async ({ request, fetch }) => {
		const form = await request.formData();
		const pool = String(form.get('pool') ?? '');
		const body = { number: Number(form.get('number') || 0), note: String(form.get('note') ?? '').trim() };
		const res = await api('POST', `/number-pools/${pool}/reservations`, { body, f: fetch });
		return res.ok ? { done: 'reserve', resPool: pool } : failed('reserve', res);
	},
	release: async ({ request, fetch }) => {
		const form = await request.formData();
		const pool = String(form.get('pool') ?? '');
		const res = await api('DELETE', `/number-pools/${pool}/reservations/${String(form.get('rid') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'release', resPool: pool } : failed('release', res);
	}
};
