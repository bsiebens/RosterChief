import { fail } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { answerFromForm } from '#lib/requirements.ts';
import type { Actions } from '@sveltejs/kit';

/** The result of a requirement action as `fail()` data for the Documents card, or `{ docDone }`. */
function docResult(action: string, res: { ok: boolean; status: number; message?: string; fields: Record<string, string> }) {
	if (res.ok) return { docDone: action };
	return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { docAction: action, docError: res.fields.__all__ || res.message || 'failed', docFields: res.fields });
}

const target = (f: FormData) => `/requirements/memberships/${String(f.get('membership_id') ?? '')}/${String(f.get('requirement_id') ?? '')}`;

/** The form actions behind `DocumentsCard` (receive a document, answer a question, waive, approve / decline / request, send a document, reopen). Spread them into a page's `actions`. */
export const requirementActions: Actions = {
	completeRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		const out = new FormData();
		out.set('note', String(f.get('note') ?? '').trim());
		const until = String(f.get('valid_until') ?? '').trim();
		if (until) out.set('valid_until', until);
		const file = f.get('document');
		if (file instanceof File && file.size > 0) out.set('document', file);
		return docResult('complete', await api('POST', `${target(f)}/complete`, { form: out, f: fetch }));
	},
	answerRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		const type = String(f.get('question_type') ?? '');
		const until = String(f.get('valid_until') ?? '').trim();
		const body = { answer: answerFromForm(type, f.getAll('answer').map(String)), note: String(f.get('note') ?? '').trim(), valid_until: until || null };
		return docResult('answer', await api('POST', `${target(f)}/answer`, { body, f: fetch }));
	},
	bypassRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('bypass', await api('POST', `${target(f)}/bypass`, { body: { note: String(f.get('note') ?? '') }, f: fetch }));
	},
	approveRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('approve', await api('POST', `${target(f)}/approve`, { f: fetch }));
	},
	declineRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('decline', await api('POST', `${target(f)}/decline`, { body: { note: String(f.get('note') ?? '').trim() }, f: fetch }));
	},
	requestRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('request', await api('POST', `${target(f)}/request`, { body: { note: String(f.get('note') ?? '').trim() }, f: fetch }));
	},
	sendRequirementFile: async ({ request, fetch }) => {
		const f = await request.formData();
		const out = new FormData();
		out.set('note', String(f.get('note') ?? '').trim());
		const file = f.get('document');
		if (file instanceof File && file.size > 0) out.set('document', file);
		return docResult('send', await api('POST', `${target(f)}/sent-file`, { form: out, f: fetch }));
	},
	removeSentFile: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('remove_sent', await api('DELETE', `${target(f)}/sent-file`, { f: fetch }));
	},
	reopenRequirement: async ({ request, fetch }) => {
		const f = await request.formData();
		return docResult('reopen', await api('POST', `${target(f)}/reopen`, { f: fetch }));
	}
};
