import { fail } from '@sveltejs/kit';
import { api, type Fetch } from '#lib/management-api.ts';

/** Run one family API call; failures come back as `fail()` data `{familyError, familyFields, familyAction}` for the page. */
export async function familyCall<T = unknown>(action: string, method: string, path: string, f: Fetch, body?: unknown) {
	const res = await api<T>(method, path, { body, f });
	if (!res.ok) return { failure: fail(res.status || 500, { familyError: res.message || 'failed', familyFields: res.fields, familyAction: action }), res };
	return { failure: null, res };
}

/**
 * Add a person to a family from the "Add parent / Add child" dialog: either an existing member (`member_id`) or, when
 * `mode=new`, a member created on the spot (`first_name`, `last_name`, optional `email` and `date_of_birth`; no login).
 * Field errors of the new member come back under their own names.
 */
export async function addPersonToFamily(action: string, familyId: string, form: FormData, f: Fetch) {
	const s = (k: string) => String(form.get(k) ?? '').trim();
	let memberId = s('member_id');
	if (s('mode') === 'new') {
		const created = await api<{ id: string }>('POST', '/members', {
			body: { first_name: s('first_name'), last_name: s('last_name'), email: s('email'), date_of_birth: s('date_of_birth') || null },
			f
		});
		if (!created.ok) return fail(created.status || 500, { familyError: created.message || 'failed', familyFields: created.fields, familyAction: action });
		memberId = created.data!.id;
	}
	return (await familyCall(action, 'POST', `/members/families/${familyId}/members`, f, { member_id: memberId, role: s('role') || 'parent' })).failure;
}
