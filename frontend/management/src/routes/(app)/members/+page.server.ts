import { apiPage, api } from '#lib/management-api.ts';
import { apiQuery, parseFilters, type MemberRow, type SeasonOption, type TeamOption } from '#lib/members/list.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const parentData = (await parent()) as unknown as { me?: { is_club_admin: boolean; can_manage_members: boolean; permissions: string[] } };
	const me = parentData.me;
	const filters = parseFilters(url.searchParams);
	const [members, teams, seasons] = await Promise.all([
		apiPage<MemberRow>('/members', apiQuery(filters), fetch),
		api<TeamOption[]>('GET', '/members/teams', { f: fetch }),
		api<SeasonOption[]>('GET', '/memberships/seasons', { f: fetch })
	]);
	return {
		filters,
		canManage: !!me?.can_manage_members,
		showFees: !!me?.can_manage_members,
		rows: members.data?.items ?? [],
		count: members.data?.count ?? 0,
		teams: teams.data ?? [],
		seasons: seasons.data ?? [],
		error: members.ok ? null : { status: members.status, message: members.message ?? null }
	};
};
