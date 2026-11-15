import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PublicForm } from '#lib/form-response.ts';
import type { PageServerLoad } from './$types';

type ParentForm = PublicForm & { member: string; submitted: number; limit: number | null };

export const load: PageServerLoad = async ({ fetch, params }) => {
	const endpoint = `/portal/forms/${encodeURIComponent(params.send)}/${encodeURIComponent(params.member)}`;
	const result = await api<ParentForm>('GET', endpoint, { f: fetch });
	if (!result.ok || !result.data) error(result.status === 404 ? 404 : result.status === 403 ? 403 : 502, result.message);
	return { form: result.data, endpoint };
};
