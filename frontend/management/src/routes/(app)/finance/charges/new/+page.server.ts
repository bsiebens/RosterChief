import { redirect } from '@sveltejs/kit';
import { badPayload, chargeHref, chargesOn, failed, parsePayload, type ChargeDetail, type ChargeIn } from '#lib/charges.ts';
import { api } from '#lib/management-api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!chargesOn(me)) redirect(303, '/');
	return {};
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<ChargeIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<ChargeDetail>('POST', '/finance/charges', { body, f: fetch });
		if (!res.ok) return failed('save', res);
		redirect(303, `${chargeHref(res.data!.id)}?created=1`);
	}
};
