import { fail } from '@sveltejs/kit';
import { api, type Fetch } from '#lib/management-api.ts';

export type GroupMember = { member_id: string; first_name: string; last_name: string; email?: string; is_admin: boolean; must_respond: boolean };
export type GroupDetail = { id: string; name: string; member_count: number; members: GroupMember[]; can_manage: boolean; can_manage_members: boolean };

/** One groups API call; failures come back as `fail()` data `{groupError, groupFields, groupAction}` for the page. */
export async function groupCall<T = unknown>(action: string, method: string, path: string, f: Fetch, body?: unknown) {
	const res = await api<T>(method, path, { body, f });
	if (!res.ok) return { failure: fail(res.status || 500, { groupError: res.message || 'failed', groupFields: res.fields, groupAction: action }), res };
	return { failure: null, res };
}
