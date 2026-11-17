import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { FollowedGame, StatsPeople } from '#lib/portal.ts';
import type { MemberGameStats } from '#lib/analytics.ts';
import type { AttendanceStats, GoalieStats, OfficiatingStats, PlayerStats } from '#lib/member-stats.ts';
import type { PageServerLoad } from './$types';

export type MyStats = { first_name: string; last_name: string; game_stats: MemberGameStats | null; player: PlayerStats | null; goalie: GoalieStats | null; attendance: AttendanceStats | null; officiating: OfficiatingStats | null; share_token: string | null };

export const load: PageServerLoad = async ({ fetch, url }) => {
	const people = await api<StatsPeople>('GET', '/portal/me/stats', { f: fetch });
	if (!people.ok || !people.data) error(people.status === 403 ? 403 : 502, people.message);
	const list = people.data.people;
	const asked = url.searchParams.get('who');
	const who = list.find((person) => person.id === asked)?.id ?? list.find((person) => person.is_self)?.id ?? list[0]?.id ?? '';
	const stats = who ? await api<MyStats>('GET', `/portal/me/stats/${who}`, { f: fetch }) : null;
	const followed = await api<FollowedGame[]>('GET', '/portal/followed', { f: fetch });
	return { people: list, available: people.data.available, who, stats: stats?.ok ? (stats.data ?? null) : null, followed: followed.ok ? (followed.data ?? []) : [] };
};
