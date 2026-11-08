import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { NewsDetail, NewsIn, NewsPublishIn, TeamOption } from '#lib/news.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const [item, teams] = await Promise.all([api<NewsDetail>('GET', `/news/${params.id}`, { f: fetch }), api<TeamOption[]>('GET', '/news/team-options', { f: fetch })]);
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, teams: teams.data ?? [], created: url.searchParams.get('created') === '1' };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status });

/** The form's JSON payload, when the page sent one (unsaved edits go along with a workflow action). */
function payloadOf(form: FormData): NewsIn | null | 'invalid' {
	const raw = form.get('payload');
	if (raw === null || raw === '') return null;
	try {
		return JSON.parse(String(raw));
	} catch {
		return 'invalid';
	}
}

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = payloadOf(await request.formData());
		if (!body || body === 'invalid') return fail(400, { op: 'save', fields: {} as Record<string, string>, message: '', status: 400 });
		const res = await api<NewsDetail>('PATCH', `/news/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	/** submit-for-review | publish | unpublish | send-back, saving the form's unsaved edits first. */
	workflow: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const op = String(form.get('op') ?? '');
		if (!['submit-for-review', 'publish', 'unpublish', 'send-back'].includes(op)) return fail(400, { op, fields: {} as Record<string, string>, message: '', status: 400 });
		const body = payloadOf(form);
		if (body === 'invalid') return fail(400, { op, fields: {} as Record<string, string>, message: '', status: 400 });
		if (body) {
			const saved = await api<NewsDetail>('PATCH', `/news/${params.id}`, { body, f: fetch });
			if (!saved.ok) return failed('save', saved);
		}
		let publish: NewsPublishIn | undefined;
		if (op === 'publish') {
			// The page converts the picked wall-clock time to an ISO instant (in the club's time zone).
			publish = { published_at: String(form.get('published_at') ?? '') || null, notify: form.get('notify') === 'on' };
		}
		const res = await api<NewsDetail>('POST', `/news/${params.id}/${op}`, { body: publish, f: fetch });
		return res.ok ? { done: op } : failed(op, res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/news/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/news?deleted=1');
	}
};
