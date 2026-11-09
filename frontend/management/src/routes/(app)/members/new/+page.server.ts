import { api } from '#lib/management-api.ts';
import { requireAdmin, saveMember } from '#lib/members/form-server.ts';
import type { Season } from '#lib/members/detail-types.ts';
import { formValuesFrom } from '#lib/members/detail-helpers.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent, fetch }) => {
	await requireAdmin(parent);
	const seasons = await api<Season[]>('GET', '/memberships/seasons', { f: fetch });
	return { initial: formValuesFrom(), seasons: seasons.data ?? [] };
};

export const actions: Actions = { default: async (event) => saveMember(event) };
