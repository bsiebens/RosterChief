import { fail } from '@sveltejs/kit';
import { api, type Fetch } from '#lib/management-api.ts';

export type Season = { id: string; name: string; is_current: boolean; start_date?: string };
export type Pool = { id: string; name: string; min_number: number; max_number: number; min_age_gap_years: number; team_ids?: string[] };
export type TeamRow = {
	id: string;
	name: string;
	short_name: string;
	pool_id: string | null;
	pool_name: string | null;
	age_min: number | null;
	age_max: number | null;
	tournament_only: boolean;
	ordering: number | null;
	feeds_into_id: string | null;
	feeds_into_name: string | null;
	/** Who arranges referees / match officials for the team's home games: the club or the federation. */
	referee_management: 'club' | 'federation';
	official_management: 'club' | 'federation';
	player_count: number;
	staff_count: number;
};
export type RosterEntry = {
	id: string;
	member_id: string;
	first_name: string;
	last_name: string;
	photo_url: string | null;
	date_of_birth: string | null;
	position_id: string | null;
	position_name: string | null;
	jersey_number: number | null;
	is_captain: boolean;
	is_alternate_captain: boolean;
};
export type StaffEntry = { id: string; member_id: string; first_name: string; last_name: string; photo_url: string | null; position_id: string | null; position_name: string | null };
export type TeamDetail = TeamRow & { season_id: string; photo_url?: string | null; analytics_available?: boolean; roster: RosterEntry[]; staff: StaffEntry[] };
export type EligibleReferee = { member_id: string; first_name: string; last_name: string; photo_url: string | null; level_name: string; valid_until: string | null };
export type Position = { id: string; name: string; short_name: string; ordering: number; staff_position: boolean; management_position: boolean; in_use_count: number };

/** One teams API call; failures come back as `fail()` data `{teamError, teamFields, teamAction}` for the page. */
export async function teamCall<T = unknown>(action: string, method: string, path: string, f: Fetch, body?: unknown) {
	const res = await api<T>(method, path, { body, f });
	if (!res.ok) return { failure: fail(res.status || 500, { teamError: res.message || 'failed', teamFields: res.fields, teamAction: action }), res };
	return { failure: null, res };
}

export const text = (form: FormData, k: string) => String(form.get(k) ?? '').trim();
export const intOrNull = (form: FormData, k: string) => {
	const v = text(form, k);
	return v === '' ? null : Number.isNaN(Number(v)) ? null : Math.trunc(Number(v));
};
export const strOrNull = (form: FormData, k: string) => text(form, k) || null;

/** The body for POST/PATCH /teams from the shared team form. */
export function teamBody(form: FormData) {
	// Pool, ages and "feeds into" need the settings permission: the form disables them for everyone else, and disabled
	// fields are not submitted, so they are left out of the body (rather than cleared).
	return {
		name: text(form, 'name'),
		short_name: text(form, 'short_name'),
		...(form.has('pool_id') ? { pool_id: strOrNull(form, 'pool_id') } : {}),
		...(form.has('age_min') ? { age_min: intOrNull(form, 'age_min') } : {}),
		...(form.has('age_max') ? { age_max: intOrNull(form, 'age_max') } : {}),
		tournament_only: form.get('tournament_only') === 'on',
		...(form.has('feeds_into_id') ? { feeds_into_id: strOrNull(form, 'feeds_into_id') } : {}),
		...(form.has('referee_management') ? { referee_management: text(form, 'referee_management') } : {}),
		...(form.has('official_management') ? { official_management: text(form, 'official_management') } : {})
	};
}

export async function loadSeasons(f: Fetch): Promise<Season[]> {
	return (await api<Season[]>('GET', '/memberships/seasons', { f })).data ?? [];
}
export async function loadPools(f: Fetch): Promise<Pool[]> {
	return (await api<Pool[]>('GET', '/number-pools', { f })).data ?? [];
}
