import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { loadPools, loadSeasons, strOrNull, teamBody, teamCall, text, intOrNull, type EligibleReferee, type Position, type TeamDetail, type TeamRow } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

export type HolderRef = { member_id: string; first_name: string; last_name: string; date_of_birth: string | null; team_id: string; team_name: string };
export type TeamNumbers = {
	season_id: string | null;
	pool: { id: string; name: string; min_number: number; max_number: number; min_age_gap_years: number } | null;
	players: { id: string; member_id: string; first_name: string; last_name: string; jersey_number: number | null }[];
	free_numbers: number[];
	taken_by_others: Record<string, HolderRef[]>;
	reserved: Record<string, string>;
};

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = (await parent()) as { me?: { features?: string[] } };
	const season = url.searchParams.get('season') ?? '';
	const [team, seasons, pools, positions, teams, numbers, referees, officials] = await Promise.all([
		api<TeamDetail>('GET', `/teams/${params.id}`, { query: { season }, f: fetch }),
		loadSeasons(fetch),
		loadPools(fetch),
		api<Position[]>('GET', '/team-positions', { f: fetch }),
		api<TeamRow[]>('GET', '/teams', { query: { season }, f: fetch }),
		api<TeamNumbers>('GET', `/teams/${params.id}/numbers`, { query: { season }, f: fetch }),
		// Eligible referees only exist for clubs with the referee_officials feature.
		me?.features?.includes('referee_officials') ? api<EligibleReferee[]>('GET', `/teams/${params.id}/referees`, { f: fetch }) : Promise.resolve(null),
		me?.features?.includes('referee_officials') ? api<EligibleReferee[]>('GET', `/teams/${params.id}/officials`, { f: fetch }) : Promise.resolve(null)
	]);
	if (!team.ok) error(team.status === 404 ? 404 : team.status || 500);
	return { team: team.data!, season, seasons, pools, positions: positions.data ?? [], teams: teams.data ?? [], numbers: numbers.ok ? (numbers.data ?? null) : null, referees: referees?.ok ? (referees.data ?? []) : null, officials: officials?.ok ? (officials.data ?? []) : null };
};

const flag = (form: FormData, k: string) => form.get(k) === 'on';
const rosterBody = (form: FormData) => ({
	position_id: strOrNull(form, 'position_id'),
	jersey_number: intOrNull(form, 'jersey_number'),
	is_captain: flag(form, 'is_captain'),
	is_alternate_captain: flag(form, 'is_alternate_captain')
});

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('save', 'PATCH', `/teams/${params.id}`, fetch, teamBody(form));
		return failure ?? { done: 'save' };
	},
	uploadPhoto: async ({ request, params, url, fetch }) => {
		const form = await request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || file.size === 0) return fail(422, { teamAction: 'photo', teamError: 'required', teamFields: {} });
		const up = new FormData();
		up.set('file', file);
		const season = url.searchParams.get('season') ?? '';
		if (season) up.set('season_id', season);
		const res = await api('POST', `/teams/${params.id}/photo`, { form: up, f: fetch });
		if (!res.ok) return fail(res.status || 500, { teamAction: 'photo', teamError: res.fields.file || res.message || 'failed', teamFields: res.fields });
		return { done: 'photo' };
	},
	removePhoto: async ({ params, url, fetch }) => {
		const { failure } = await teamCall('photo', 'DELETE', `/teams/${params.id}/photo${url.searchParams.get('season') ? `?season=${url.searchParams.get('season')}` : ''}`, fetch);
		return failure ?? { done: 'removePhoto' };
	},
	delete: async ({ params, fetch }) => {
		const { failure } = await teamCall('delete', 'DELETE', `/teams/${params.id}`, fetch);
		if (failure) return failure;
		redirect(303, '/teams');
	},
	addPlayer: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const body = { member_id: text(form, 'member_id'), season_id: strOrNull(form, 'season_id'), ...rosterBody(form) };
		const { failure } = await teamCall('addPlayer', 'POST', `/teams/${params.id}/roster`, fetch, body);
		return failure ?? { done: 'addPlayer' };
	},
	editPlayer: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('editPlayer', 'PATCH', `/teams/${params.id}/roster/${text(form, 'membership_id')}`, fetch, rosterBody(form));
		return failure ?? { done: 'editPlayer' };
	},
	removePlayer: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('removePlayer', 'DELETE', `/teams/${params.id}/roster/${text(form, 'membership_id')}`, fetch);
		return failure ?? { done: 'removePlayer' };
	},
	addStaff: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const body = { member_id: text(form, 'member_id'), season_id: strOrNull(form, 'season_id'), position_id: text(form, 'position_id') };
		const { failure } = await teamCall('addStaff', 'POST', `/teams/${params.id}/staff`, fetch, body);
		return failure ?? { done: 'addStaff' };
	},
	editStaff: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('editStaff', 'PATCH', `/teams/${params.id}/staff/${text(form, 'assignment_id')}`, fetch, { position_id: text(form, 'position_id') });
		return failure ?? { done: 'editStaff' };
	},
	removeStaff: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await teamCall('removeStaff', 'DELETE', `/teams/${params.id}/staff/${text(form, 'assignment_id')}`, fetch);
		return failure ?? { done: 'removeStaff' };
	}
};
