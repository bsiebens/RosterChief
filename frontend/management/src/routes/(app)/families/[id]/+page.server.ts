import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { addPersonToFamily, familyCall } from '#lib/families/server.ts';
import { grantLoginFor, requireAdmin } from '#lib/members/form-server.ts';
import type { FamilyDetail } from '#lib/members/detail-types.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch, parent }) => {
	await requireAdmin(parent, '/members');
	const res = await api<FamilyDetail>('GET', `/members/families/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status === 404 ? 404 : res.status || 500);
	return { family: res.data! };
};

const text = (form: FormData, k: string) => String(form.get(k) ?? '').trim();

export const actions: Actions = {
	rename: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await familyCall('rename', 'PATCH', `/members/families/${params.id}`, fetch, { name: text(form, 'name') });
		return failure ?? { done: 'rename' };
	},
	delete: async ({ params, fetch }) => {
		const { failure } = await familyCall('delete', 'DELETE', `/members/families/${params.id}`, fetch);
		if (failure) return failure;
		redirect(303, '/families');
	},
	addMember: async ({ request, params, fetch }) => {
		const form = await request.formData();
		return (await addPersonToFamily('addMember', params.id, form, fetch)) ?? { done: 'addMember' };
	},
	setRole: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await familyCall('setRole', 'PATCH', `/members/families/${params.id}/members/${text(form, 'member_id')}`, fetch, { role: text(form, 'role') });
		return failure ?? { done: 'setRole' };
	},
	removeMember: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await familyCall('removeMember', 'DELETE', `/members/families/${params.id}/members/${text(form, 'member_id')}`, fetch);
		return failure ?? { done: 'removeMember' };
	},
	grantLogin: async ({ request, fetch }) => {
		const form = await request.formData();
		const r = await grantLoginFor(text(form, 'member_id'), text(form, 'email'), fetch);
		if (!r.ok) return fail(r.status, { familyError: r.message || Object.values(r.fields)[0] || 'failed', familyFields: r.fields, familyAction: 'grantLogin' });
		return { done: 'grantLogin', granted: { email: r.email, created: r.created, emailSent: r.emailSent } };
	},
	deleteMember: async ({ request, fetch }) => {
		const form = await request.formData();
		const detach = form.get('detach_login') === 'on';
		const { failure } = await familyCall('deleteMember', 'DELETE', `/members/${text(form, 'member_id')}${detach ? '?detach_login=true' : ''}`, fetch);
		return failure ?? { done: 'deleteMember' };
	}
};
