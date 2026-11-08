import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { blankNews, newsHref, type NewsDetail, type NewsIn, type TeamOption } from '#lib/news.ts';
import type { Actions, PageServerLoad } from './$types';

/** Add a news item (saved as a draft). Anyone who may add news; others go back to the overview. */
export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_add_news) redirect(303, '/');
	const teams = await api<TeamOption[]>('GET', '/news/team-options', { f: fetch });
	return { teams: teams.data ?? [], initial: blankNews() };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		let body: NewsIn;
		try {
			body = JSON.parse(String(form.get('payload') ?? ''));
		} catch {
			return fail(400, { fields: {} as Record<string, string>, message: 'Invalid data.', status: 400 });
		}
		const res = await api<NewsDetail>('POST', '/news', { body, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.message ?? '', status: res.status });
		redirect(303, `${newsHref(res.data!.id)}?created=1`);
	}
};
