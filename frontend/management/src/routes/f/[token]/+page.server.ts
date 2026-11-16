import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PageServerLoad } from './$types';

import type { PublicForm } from '#lib/form-response.ts';

/** GET /public/forms/{token}: no login. Unknown, non-public and login-required forms are a plain 404. */
export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicForm>('GET', `/public/forms/${encodeURIComponent(params.token)}`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { form: res.data };
};
