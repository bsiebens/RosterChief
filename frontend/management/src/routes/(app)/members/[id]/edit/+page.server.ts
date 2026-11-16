import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireAdmin, saveMember } from '#lib/members/form-server.ts';
import { formValuesFrom } from '#lib/members/detail-helpers.ts';
import type { MemberDetail } from '#lib/members/detail-types.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent, params, fetch }) => {
	await requireAdmin(parent, `/members/${params.id}`);
	const res = await api<MemberDetail>('GET', `/members/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status === 404 ? 404 : res.status || 500);
	const m = res.data!;
	return { id: m.id, name: `${m.first_name} ${m.last_name}`.trim(), hasLogin: m.has_login, profilePhotoUrl: m.profile_photo_url, playerPhotoUrl: m.player_photo_url, initial: formValuesFrom(m) };
};

export const actions: Actions = {
	// Named actions only: SvelteKit forbids a default action next to named ones.
	save: async (event) => saveMember(event, event.params.id),
	delete: async ({ request, params, fetch }) => {
		const detach = (await request.formData()).get('detach_login') === 'on';
		const res = await api('DELETE', `/members/${params.id}`, { query: detach ? { detach_login: true } : {}, f: fetch });
		if (!res.ok) return fail(res.status || 500, { deleteError: res.message || res.fields.detail || 'failed' });
		redirect(303, '/members');
	}
};
