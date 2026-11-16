import { fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { Role, RoleDetail } from '#lib/roles.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_roles) redirect(303, '/');
	const tab = url.searchParams.get('tab') === 'people' ? 'people' : 'roles';
	// The roles list (with its counts) always; the people behind every role only for their tab.
	const [roles, people] = await Promise.all([api<Role[]>('GET', '/roles', { f: fetch }), tab === 'people' ? api<RoleDetail[]>('GET', '/roles/assignments', { f: fetch }) : Promise.resolve(null)]);
	return { tab, roles: roles.data ?? [], people: people?.data ?? [], open: url.searchParams.get('open') ?? '', error: roles.ok && (people?.ok ?? true) ? null : roles.status || people?.status || 500 };
};

const failed = (op: string, res: ApiCall<unknown>, roleId: string) => fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op, roleId, message: res.fields.member_id || res.fields.__all__ || res.message || '' });

export const actions: Actions = {
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('role_id') ?? '');
		const res = await api('DELETE', `/roles/${id}`, { f: fetch });
		return res.ok ? { done: 'delete', roleId: id } : failed('delete', res, id);
	},
	addMember: async ({ request, fetch }) => {
		const f = await request.formData();
		const roleId = String(f.get('role_id') ?? '');
		const memberId = String(f.get('member_id') ?? '');
		if (!memberId) return fail(422, { op: 'addMember', roleId, message: '' });
		const res = await api('POST', `/roles/${roleId}/members`, { body: { member_id: memberId }, f: fetch });
		return res.ok ? { done: 'addMember', roleId } : failed('addMember', res, roleId);
	},
	removeMember: async ({ request, fetch }) => {
		const f = await request.formData();
		const roleId = String(f.get('role_id') ?? '');
		const res = await api('DELETE', `/roles/${roleId}/members/${String(f.get('member_id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'removeMember', roleId } : failed('removeMember', res, roleId);
	}
};
