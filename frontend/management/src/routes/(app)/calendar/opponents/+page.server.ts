import { fail } from '@sveltejs/kit';
import type { Opponent } from '#lib/calendar.ts';
import { api } from '#lib/management-api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const q = (url.searchParams.get('q') ?? '').trim();
	const res = await api<Opponent[]>('GET', '/opponents', { query: { q }, f: fetch });
	return { q, opponents: res.data ?? [], error: res.ok ? null : res.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { oppAction: action, oppError: res.fields.__all__ || res.message || 'failed', oppFields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const body = {
			name: String(form.get('name') ?? '').trim(),
			// One alternative name per line.
			alternative_names: String(form.get('alternative_names') ?? '').split('\n').map((x) => x.trim()).filter(Boolean)
		};
		const res = id ? await api<Opponent>('PATCH', `/opponents/${id}`, { body, f: fetch }) : await api<Opponent>('POST', '/opponents', { body, f: fetch });
		if (!res.ok) return failed('save', res);
		const opponentId = res.data!.id;
		const logo = form.get('logo');
		if (logo instanceof File && logo.size > 0) {
			const up = new FormData();
			up.set('file', logo);
			const pr = await api('POST', `/opponents/${opponentId}/logo`, { form: up, f: fetch });
			if (!pr.ok) return failed('save', { ...pr, fields: { logo: pr.fields.file || pr.message || '' } });
		} else if (form.get('remove_logo') === 'on') {
			const pr = await api('DELETE', `/opponents/${opponentId}/logo`, { f: fetch });
			if (!pr.ok) return failed('save', { ...pr, fields: { logo: pr.message || '' } });
		}
		return { done: 'save' };
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/opponents/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
