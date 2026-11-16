import { fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import type { Actions, PageServerLoad } from './$types';

export type Position = { id: string; name: string; short_name: string; ordering: number; staff_position: boolean; management_position: boolean; in_use_count: number };

export const load: PageServerLoad = async ({ fetch, parent }) => {
	await requireSettings(parent);
	const res = await api<Position[]>('GET', '/team-positions', { f: fetch });
	return { positions: res.data ?? [], error: res.ok ? null : res.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { posAction: action, posError: res.message || 'failed', posFields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const staff = form.get('staff_position') === 'on';
		const body = {
			name: String(form.get('name') ?? '').trim(),
			short_name: String(form.get('short_name') ?? '').trim(),
			staff_position: staff,
			management_position: staff && form.get('management_position') === 'on'
		};
		// New positions go to the end of the list; editing never changes the order (drag/arrows do).
		if (!id) Object.assign(body, { ordering: Number(form.get('ordering') || 0) });
		const res = id ? await api('PATCH', `/team-positions/${id}`, { body, f: fetch }) : await api('POST', '/team-positions', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	reorder: async ({ request, fetch }) => {
		const form = await request.formData();
		const ids = String(form.get('ids') ?? '').split(',').filter(Boolean);
		const res = await api('POST', '/team-positions/reorder', { body: { ids }, f: fetch });
		return res.ok ? { done: 'reorder' } : failed('reorder', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/team-positions/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
