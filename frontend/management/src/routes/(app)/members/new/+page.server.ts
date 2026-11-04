import { requireAdmin, saveMember } from '#lib/members/form-server.ts';
import { formValuesFrom } from '#lib/members/detail-helpers.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	await requireAdmin(parent);
	return { initial: formValuesFrom() };
};

export const actions: Actions = { default: async (event) => saveMember(event) };
