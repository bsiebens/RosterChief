import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { parseOptionLines, type Requirement, type RequirementListItem } from '#lib/requirements.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_members) redirect(303, '/');
	const res = await api<RequirementListItem[]>('GET', '/requirements', { f: fetch });
	return { requirements: res.data ?? [], error: res.ok ? null : res.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { reqAction: action, reqError: res.fields.__all__ || res.message || 'failed', reqFields: res.fields });

const whole = (v: FormDataEntryValue | null) => {
	const s = String(v ?? '').trim();
	return s === '' ? null : Number(s);
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const f = await request.formData();
		const id = String(f.get('id') ?? '');
		const kind = f.get('kind') === 'question' ? 'question' : 'document';
		const questionType = kind === 'question' ? String(f.get('question_type') ?? '') : '';
		const decimal = (v: FormDataEntryValue | null) => {
			const s = String(v ?? '').trim().replace(',', '.');
			return s === '' ? null : s;
		};
		const body = {
			name: String(f.get('name') ?? '').trim(),
			description: String(f.get('description') ?? '').trim(),
			kind,
			question_type: questionType,
			options: kind === 'question' && (questionType === 'choice' || questionType === 'multichoice') ? parseOptionLines(String(f.get('options') ?? '')) : null,
			min_value: questionType === 'number' ? decimal(f.get('min_value')) : null,
			max_value: questionType === 'number' ? decimal(f.get('max_value')) : null,
			requires_document: kind === 'document' && f.get('requires_document') === 'on',
			needs_staff_file: kind === 'document' && f.get('needs_staff_file') === 'on',
			member_instructions: String(f.get('member_instructions') ?? '').trim(),
			valid_for_months: whole(f.get('valid_for_months')),
			reminder_days_before: whole(f.get('reminder_days_before')) ?? 30,
			blocked_event_kinds: f.getAll('blocked_event_kinds').map(String),
			is_active: f.get('is_active') === 'on'
		};
		const res = id ? await api<Requirement>('PATCH', `/requirements/${id}`, { body, f: fetch }) : await api<Requirement>('POST', '/requirements', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/requirements/${id}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
