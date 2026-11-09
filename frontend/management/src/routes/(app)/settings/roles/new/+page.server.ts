import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { roleHref, type Permission, type RoleDetail } from '#lib/roles.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_roles) redirect(303, '/');
	const perms = await api<Permission[]>('GET', '/roles/permissions', { f: fetch });
	return { permissions: perms.data ?? [] };
};

export const actions: Actions = {
	default: async ({ request, fetch }) => {
		const f = await request.formData();
		const values = { name: String(f.get('name') ?? '').trim(), description: String(f.get('description') ?? '').trim(), permissions: f.getAll('permissions').map(String) };
		const res = await api<RoleDetail>('POST', '/roles', { body: values, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { values, fields: res.fields, message: res.fields.__all__ || res.message || '' });
		redirect(303, `${roleHref(res.data!.id)}?created=1`);
	}
};
