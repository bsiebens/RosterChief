import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { loadPools, loadSeasons, teamBody, teamCall, type TeamRow } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const q = (url.searchParams.get('q') ?? '').trim();
	const season = url.searchParams.get('season') ?? '';
	const coach = url.searchParams.get('coach') === 'none' ? 'none' : '';
	const [teams, seasons, pools] = await Promise.all([api<TeamRow[]>('GET', '/teams', { query: { q, season, coach }, f: fetch }), loadSeasons(fetch), loadPools(fetch)]);
	return { filters: { q, season, coach }, rows: teams.data ?? [], seasons, pools, error: teams.ok ? null : teams.status };
};

export const actions: Actions = {
	save: async ({ request, url, fetch }) => {
		const form = await request.formData();
		const id = url.searchParams.get('id') ?? '';
		const { failure } = await teamCall('save', 'PATCH', `/teams/${id}`, fetch, teamBody(form));
		return failure ?? { done: 'save' };
	},
	reorder: async ({ request, fetch }) => {
		const form = await request.formData();
		const ids = String(form.get('ids') ?? '').split(',').filter(Boolean);
		const { failure } = await teamCall('reorder', 'POST', '/teams/reorder', fetch, { ids });
		return failure ?? { done: 'reorder' };
	},
	create: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure, res } = await teamCall<TeamRow>('create', 'POST', '/teams', fetch, teamBody(form));
		if (failure) return failure;
		redirect(303, `/teams/${res.data!.id}`);
	}
};
