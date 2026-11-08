import { fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import type { Actions, PageServerLoad } from './$types';

export type EventSubtype = { id: string; kind: string; kind_label: string; name: string; title_template: string; ordering: number; in_use_count: number };

export const load: PageServerLoad = async ({ fetch, parent }) => {
	await requireSettings(parent);
	const res = await api<EventSubtype[]>('GET', '/event-subtypes', { f: fetch });
	return { subtypes: res.data ?? [], error: res.ok ? null : res.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { subAction: action, subError: res.message || 'failed', subFields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const body = {
			kind: String(form.get('kind') ?? ''),
			name: String(form.get('name') ?? '').trim(),
			title_template: String(form.get('title_template') ?? '').trim()
		};
		const res = id ? await api('PATCH', `/event-subtypes/${id}`, { body, f: fetch }) : await api('POST', '/event-subtypes', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	reorder: async ({ request, fetch }) => {
		const form = await request.formData();
		const ids = String(form.get('ids') ?? '').split(',').filter(Boolean);
		const res = await api('POST', '/event-subtypes/reorder', { body: { ids }, f: fetch });
		return res.ok ? { done: 'reorder' } : failed('reorder', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/event-subtypes/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
