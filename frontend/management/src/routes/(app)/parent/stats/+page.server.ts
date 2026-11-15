import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { StatsPeople } from '#lib/portal.ts';
import type { MemberGameStats } from '#lib/analytics.ts';
import type { GoalieStats, PlayerStats } from '#lib/member-stats.ts';
import type { PageServerLoad } from './$types';

export type MyStats = { first_name: string; last_name: string; game_stats: MemberGameStats; player: PlayerStats | null; goalie: GoalieStats | null; share_token: string | null };

export const load: PageServerLoad = async ({ fetch, url }) => {
	const people = await api<StatsPeople>('GET', '/portal/me/stats', { f: fetch });
	if (!people.ok || !people.data) error(people.status === 403 ? 403 : 502, people.message);
	const list = people.data.people;
	const asked = url.searchParams.get('who');
	const who = list.find((person) => person.id === asked)?.id ?? list.find((person) => person.is_self)?.id ?? list[0]?.id ?? '';
	const stats = people.data.available && who ? await api<MyStats>('GET', `/portal/me/stats/${who}`, { f: fetch }) : null;
	return { people: list, available: people.data.available, who, stats: stats?.ok ? (stats.data ?? null) : null };
};
