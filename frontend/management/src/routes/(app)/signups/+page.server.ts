import { fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import { requirementActions } from '#lib/requirements-server.ts';
import type { Season } from '#lib/members/detail-types.ts';
import { SIGNUP_FILTERS, type SignupFilter, type SignupList, type SignupOptions } from '#lib/signups.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_members) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const asked = url.searchParams.get('only') as SignupFilter;
	const only: SignupFilter = SIGNUP_FILTERS.includes(asked) ? asked : 'all';
	const season = url.searchParams.get('season') ?? '';
	const [list, seasons, options] = await Promise.all([
		api<SignupList>('GET', '/signups', { query: { q, season_id: season || undefined }, f: fetch }),
		api<Season[]>('GET', '/memberships/seasons', { f: fetch }),
		api<SignupOptions>('GET', '/signups/options', { f: fetch })
	]);
	const all = list.data?.results ?? [];
	const rows = only === 'all' ? all : all.filter((r) => (only === 'clean') === r.clean);
	return {
		filters: { q, only, season },
		rows,
		total: all.length,
		/** Everyone who is ready, whatever tab is open: who "approve all" will activate. */
		cleanRows: all.filter((r) => r.clean),
		clean: list.data?.clean_count ?? 0,
		seasonName: list.data?.season_name ?? null,
		seasons: seasons.data ?? [],
		options: options.data ?? { teams: [], positions: [] },
		error: list.ok ? null : list.status
	};
};

const failed = (op: string, res: ApiCall<unknown>, id = '') =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op, id, code: res.code, message: res.fields.__all__ || (res.fields.requirements ? `requirements:${res.fields.requirements}` : '') || res.message || '', fields: res.fields });

export const actions: Actions = {
	...requirementActions,
	approve: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('POST', `/signups/${id}/approve`, { f: fetch });
		return res.ok ? { done: 'approve', id } : failed('approve', res, id);
	},
	approveClean: async ({ request, fetch }) => {
		const season = String((await request.formData()).get('season') ?? '');
		const res = await api<{ approved: number }>('POST', '/signups/approve-clean', { body: { season_id: season || null }, f: fetch });
		return res.ok ? { done: 'approveClean', approved: res.data?.approved ?? 0 } : failed('approveClean', res);
	},
	place: async ({ request, fetch }) => {
		const f = await request.formData();
		const id = String(f.get('id') ?? '');
		const jersey = String(f.get('jersey_number') ?? '').trim();
		const body = { team_id: String(f.get('team_id') ?? ''), position_id: String(f.get('position_id') ?? '') || null, jersey_number: jersey === '' ? null : Number(jersey) };
		const res = await api('POST', `/signups/${id}/place`, { body, f: fetch });
		return res.ok ? { done: 'place', id } : failed('place', res, id);
	},
	link: async ({ request, fetch }) => {
		const f = await request.formData();
		const id = String(f.get('id') ?? '');
		const res = await api('POST', `/signups/${id}/link`, { body: { member_id: String(f.get('member_id') ?? '') }, f: fetch });
		return res.ok ? { done: 'link', id } : failed('link', res, id);
	},
	cancel: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api<{ result: string }>('POST', `/signups/${id}/cancel`, { f: fetch });
		return res.ok ? { done: 'cancel', id, result: res.data?.result ?? 'cancelled' } : failed('cancel', res, id);
	}
};
