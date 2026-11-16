import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { Permission, RoleDetail } from '#lib/roles.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_roles) redirect(303, '/');
	const [role, perms] = await Promise.all([api<RoleDetail>('GET', `/roles/${params.id}`, { f: fetch }), api<Permission[]>('GET', '/roles/permissions', { f: fetch })]);
	if (!role.ok) error(role.status >= 400 && role.status < 600 ? role.status : 500, role.message);
	return { role: role.data!, permissions: perms.data ?? [], created: url.searchParams.get('created') === '1' };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, code: res.code ?? '', fields: res.fields, message: res.fields.__all__ || res.fields.member_id || res.message || '' });

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const body = { name: String(f.get('name') ?? '').trim(), description: String(f.get('description') ?? '').trim(), permissions: f.getAll('permissions').map(String) };
		const res = await api('PATCH', `/roles/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/roles/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/settings/roles?deleted=1');
	}
};
