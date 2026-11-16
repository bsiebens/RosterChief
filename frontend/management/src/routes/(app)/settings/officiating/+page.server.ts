import { error, fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import type { NamedRef } from '#lib/members/eligibility.ts';
import type { Actions, PageServerLoad } from './$types';

export type Level = { id: string; name: string; team_ids: string[]; inherits_from_id: string | null; member_count: number; inherited_by_count: number };
const KINDS = ['referee', 'official'] as const;
type Kind = (typeof KINDS)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	await requireSettings(parent);
	const { me } = (await parent()) as { me: { features: string[] } };
	if (!me.features.includes('referee_officials')) error(404);
	const tab: Kind = url.searchParams.get('tab') === 'official' ? 'official' : 'referee';
	const [levels, opts] = await Promise.all([api<Level[]>('GET', `/officiating/${tab}-levels`, { f: fetch }), api<{ teams: NamedRef[] }>('GET', '/members/officiating/options', { f: fetch })]);
	return { tab, levels: levels.data ?? [], teams: opts.data?.teams ?? [], error: levels.ok ? null : levels.status };
};

const kindOf = (form: FormData): Kind => (form.get('kind') === 'official' ? 'official' : 'referee');
const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { levelAction: action, levelError: res.message || 'failed', levelFields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const body = {
			name: String(form.get('name') ?? '').trim(),
			team_ids: form.getAll('team_ids').map(String),
			inherits_from_id: String(form.get('inherits_from_id') ?? '') || null
		};
		const base = `/officiating/${kindOf(form)}-levels`;
		const res = id ? await api('PATCH', `${base}/${id}`, { body, f: fetch }) : await api('POST', base, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/officiating/${kindOf(form)}-levels/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
