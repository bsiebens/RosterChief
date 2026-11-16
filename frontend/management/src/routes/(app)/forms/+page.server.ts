import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, formHref, type FormDetail, type FormListItem, type PagedList } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const ACTIVE = ['all', 'active', 'inactive'] as const;
type Active = (typeof ACTIVE)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const requested = url.searchParams.get('active') as Active;
	const active: Active = ACTIVE.includes(requested) ? requested : 'all';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<PagedList<FormListItem> | FormListItem[]>('GET', '/forms', {
		query: { q, active: active === 'all' ? undefined : active === 'active', limit: PAGE_SIZE, offset: offset || undefined },
		f: fetch
	});
	const list = asList(res.data);
	return { filters: { q, active, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);

export const actions: Actions = {
	duplicate: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const title = String(form.get('title') ?? '').trim();
		const res = await api<FormDetail>('POST', `/forms/${id}/duplicate`, { body: title ? { title } : {}, f: fetch });
		if (!res.ok) return fail(httpStatus(res.status), { op: 'duplicate', id, code: res.code ?? '', message: res.message ?? '', fields: res.fields });
		redirect(303, `${formHref(res.data!.id)}?duplicated=1`);
	},
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/forms/${id}`, { f: fetch });
		if (!res.ok) return fail(httpStatus(res.status), { op: 'delete', id, code: res.code ?? '', message: res.message ?? '', fields: res.fields });
		return { done: 'delete', id };
	},
	/** The fallback when a form with responses cannot be deleted. */
	deactivate: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api<FormDetail>('PATCH', `/forms/${id}`, { body: { is_active: false }, f: fetch });
		if (!res.ok) return fail(httpStatus(res.status), { op: 'deactivate', id, code: res.code ?? '', message: res.message ?? '', fields: res.fields });
		return { done: 'deactivate', id };
	}
};
