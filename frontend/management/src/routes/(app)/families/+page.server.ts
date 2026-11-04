import { redirect } from '@sveltejs/kit';
import { apiPage } from '#lib/management-api.ts';
import { familyCall } from '#lib/families/server.ts';
import { requireAdmin } from '#lib/members/form-server.ts';
import type { FamilyDetail } from '#lib/members/detail-types.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	await requireAdmin(parent, '/members');
	const q = (url.searchParams.get('q') ?? '').trim();
	const role = url.searchParams.get('role') ?? '';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await apiPage<FamilyDetail>('/members/families', { q, role, limit: PAGE_SIZE, offset: offset || undefined }, fetch);
	return { filters: { q, role, offset }, rows: res.data?.items ?? [], count: res.data?.count ?? 0, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	create: async ({ request, fetch }) => {
		const form = await request.formData();
		const s = (k: string) => String(form.get(k) ?? '').trim();
		const memberId = s('member_id');
		const { failure, res } = await familyCall<FamilyDetail>('create', 'POST', '/members/families', fetch, {
			name: s('name'),
			members: memberId ? [{ member_id: memberId, role: s('role') || 'parent' }] : []
		});
		if (failure) return failure;
		redirect(303, `/families/${res.data!.id}`);
	}
};
