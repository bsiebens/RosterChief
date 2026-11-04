import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { groupCall, type GroupDetail } from '#lib/groups/server.ts';
import { requireAdmin } from '#lib/members/form-server.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch, parent }) => {
	await requireAdmin(parent, '/members');
	const res = await api<GroupDetail>('GET', `/members/groups/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status === 404 ? 404 : res.status || 500);
	return { group: res.data! };
};

const text = (form: FormData, k: string) => String(form.get(k) ?? '').trim();

export const actions: Actions = {
	rename: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await groupCall('rename', 'PATCH', `/members/groups/${params.id}`, fetch, { name: text(form, 'name') });
		return failure ?? { done: 'rename' };
	},
	delete: async ({ params, fetch }) => {
		const { failure } = await groupCall('delete', 'DELETE', `/members/groups/${params.id}`, fetch);
		if (failure) return failure;
		redirect(303, '/groups');
	},
	addMember: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await groupCall('addMember', 'POST', `/members/groups/${params.id}/members/${text(form, 'member_id')}`, fetch, {});
		return failure ?? { done: 'addMember' };
	},
	removeMember: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const { failure } = await groupCall('removeMember', 'DELETE', `/members/groups/${params.id}/members/${text(form, 'member_id')}`, fetch);
		return failure ?? { done: 'removeMember' };
	}
};
