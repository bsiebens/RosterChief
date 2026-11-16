import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { MemberGameStats } from '#lib/analytics.ts';
import type { GoalieStats, PlayerStats } from '#lib/member-stats.ts';
import type { PageServerLoad } from './$types';

/** GET /public/player-stats/{token}: no login. Unknown, switched-off or replaced links are a plain 404. */
export type PublicPlayerStats = { first_name: string; last_name: string; game_stats: MemberGameStats; player: PlayerStats | null; goalie: GoalieStats | null };

export const load: PageServerLoad = async ({ params, fetch }) => {
	const res = await api<PublicPlayerStats>('GET', `/public/player-stats/${encodeURIComponent(params.token)}`, { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { stats: res.data };
};
