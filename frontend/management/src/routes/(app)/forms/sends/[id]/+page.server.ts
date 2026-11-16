import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { Report, SendDetail, SendPatch } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	const [send, report] = await Promise.all([api<SendDetail>('GET', `/form-sends/${params.id}`, { f: fetch }), api<Report>('GET', `/form-sends/${params.id}/report`, { f: fetch })]);
	if (!send.ok) error(send.status >= 400 && send.status < 600 ? send.status : 500, send.message);
	return { send: send.data!, report: report.data ?? null, reportError: report.ok ? null : report.status, created: url.searchParams.get('created') === '1', origin: url.origin };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, code: res.code ?? '', fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status });

export const actions: Actions = {
	close: async ({ params, fetch }) => {
		const res = await api('POST', `/form-sends/${params.id}/close`, { f: fetch });
		return res.ok ? { done: 'close' } : failed('close', res);
	},
	reopen: async ({ params, fetch }) => {
		const res = await api('POST', `/form-sends/${params.id}/reopen`, { f: fetch });
		return res.ok ? { done: 'reopen' } : failed('reopen', res);
	},
	/** Window, limit and public link; the page sends the times already converted to ISO instants. */
	update: async ({ request, params, fetch }) => {
		let body: SendPatch;
		try {
			body = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'update', code: '', fields: {} as Record<string, string>, message: '', status: 400 });
		}
		const res = await api<SendDetail>('PATCH', `/form-sends/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'update' } : failed('update', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/form-sends/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/forms/sends?deleted=1');
	},
	deleteSubmission: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/form-submissions/${id}`, { f: fetch });
		return res.ok ? { done: 'deleteSubmission' } : failed('deleteSubmission', res);
	}
};
