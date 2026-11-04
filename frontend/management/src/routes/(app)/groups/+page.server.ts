import { redirect } from '@sveltejs/kit';
import { apiPage } from '#lib/management-api.ts';
import { groupCall, type GroupDetail } from '#lib/groups/server.ts';
import { requireAdmin } from '#lib/members/form-server.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	await requireAdmin(parent, '/members');
	const q = (url.searchParams.get('q') ?? '').trim();
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await apiPage<GroupDetail>('/members/groups', { q, limit: PAGE_SIZE, offset: offset || undefined }, fetch);
	return { filters: { q, offset }, rows: res.data?.items ?? [], count: res.data?.count ?? 0, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	create: async ({ request, fetch }) => {
		const form = await request.formData();
		const { failure, res } = await groupCall<GroupDetail>('create', 'POST', '/members/groups', fetch, { name: String(form.get('name') ?? '').trim() });
		if (failure) return failure;
		redirect(303, `/groups/${res.data!.id}`);
	}
};
