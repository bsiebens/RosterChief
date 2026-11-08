import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PageServerLoad } from './$types';

export type PublicFieldType = 'text' | 'textarea' | 'number' | 'email' | 'date' | 'choice' | 'multichoice' | 'checkbox' | 'file';
export type PublicField = { id: string; key: string; label: string; type: PublicFieldType; required: boolean; help_text: string; section: string; options: { value: string; label: string }[] };
export type PublicFormStatus = 'open' | 'closed' | 'not_open_yet' | 'inactive';
export type PublicForm = {
	title: string;
	description: string;
	state: { status: PublicFormStatus; opens_at: string | null; closes_at: string | null };
	fields: PublicField[];
	max_file_mb: number;
	allowed_file_extensions: string[];
};

/** GET /public/forms/{token}: no login. Unknown, non-public and login-required forms are a plain 404. */
export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicForm>('GET', `/public/forms/${encodeURIComponent(params.token)}`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { form: res.data };
};
