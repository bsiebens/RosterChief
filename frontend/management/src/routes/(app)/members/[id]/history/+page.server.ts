import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { HistoryEntry, MemberDetail, ShellMe } from '#lib/members/detail-types.ts';
import type { PageServerLoad } from './$types';

const LIMIT = 25;

export const load: PageServerLoad = async ({ params, fetch, parent, url }) => {
	const { me } = (await parent()) as { me?: ShellMe };
	if (!me?.can_manage_members) redirect(303, `/members/${params.id}`);
	const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0);
	const [member, history] = await Promise.all([
		api<MemberDetail>('GET', `/members/${params.id}`, { f: fetch }),
		api<{ items: HistoryEntry[]; count: number }>('GET', `/members/${params.id}/history`, { query: { limit: LIMIT, offset }, f: fetch })
	]);
	if (!member.ok) error(member.status === 404 ? 404 : member.status || 500);
	if (!history.ok) error(history.status === 404 ? 404 : history.status || 500);
	const m = member.data!;
	return { id: m.id, name: `${m.first_name} ${m.last_name}`.trim(), email: m.email, items: history.data!.items, count: history.data!.count, offset, limit: LIMIT };
};
