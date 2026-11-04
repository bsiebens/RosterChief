import { fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireAdmin } from '#lib/members/form-server.ts';
import { loadSeasons } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

export type ImportRow = {
	line: number;
	first_name: string;
	last_name: string;
	email: string;
	phone: string;
	emergency_phone: string;
	date_of_birth: string;
	language: string;
	license_number: string;
	team: string;
	family: string;
	family_role: string;
	status: 'new' | 'duplicate' | 'error' | 'created';
	errors: Record<string, string[]>;
	note: string;
};
export type ImportPreview = { season_id: string | null; counts: { new: number; duplicate: number; error: number }; rows: ImportRow[] };
export type ImportResult = { counts: { created: number; duplicate: number; error: number }; rows: ImportRow[] };

export const load: PageServerLoad = async ({ parent, fetch }) => {
	await requireAdmin(parent, '/members');
	return { seasons: await loadSeasons(fetch) };
};

export const actions: Actions = {
	preview: async ({ request, fetch }) => {
		const form = await request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || file.size === 0) return fail(422, { step: 'upload', error: '', fields: { file: 'required' } });
		const up = new FormData();
		up.set('file', file);
		const season = String(form.get('season_id') ?? '');
		if (season) up.set('season_id', season);
		const res = await api<ImportPreview>('POST', '/members/import/preview', { form: up, f: fetch });
		if (!res.ok) return fail(res.status === 403 ? 403 : 422, { step: 'upload', error: res.fields.file || res.message || '', fields: res.fields });
		return { step: 'preview', preview: res.data!, filename: file.name, seasonId: season };
	},
	commit: async ({ request, fetch }) => {
		const form = await request.formData();
		let rows: unknown;
		try {
			rows = JSON.parse(String(form.get('rows') ?? '[]'));
		} catch {
			return fail(400, { step: 'upload', error: 'Invalid data.', fields: {} });
		}
		const season = String(form.get('season_id') ?? '');
		const res = await api<ImportResult>('POST', '/members/import/commit', { body: { season_id: season || null, rows }, f: fetch });
		if (!res.ok) return fail(res.status === 403 ? 403 : 422, { step: 'upload', error: res.message || '', fields: res.fields, code: res.code });
		return { step: 'done', result: res.data! };
	}
};
