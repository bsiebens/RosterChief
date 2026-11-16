/** Shapes of `GET /members/{id}/stats` (api/schemas_member_stats.py) and small formatting helpers for the Stats tab. */
import { getLang } from '#lib/i18n.svelte.ts';

export type StatGame = { id: string; title: string; start: string; opponent: string | null; is_home_game: boolean; score_for: number | null; score_against: number | null };
export type Season = { id: string; name: string } | null;
export type SeasonLine = { season: Season; games: number; goals: number; assists: number; points: number; penalty_minutes: number };
export type FormGame = { event: StatGame; goals: number; assists: number; points: number; penalty_minutes: number };
export type Split = { games: number; goals: number; assists: number; points: number };
export type PlayerStats = {
	seasons: SeasonLine[];
	form: FormGame[];
	home: Split;
	away: Split;
	goals_per_game: number;
	points_per_game: number;
	current_point_streak: number;
	longest_point_streak: number;
	multi_point_games: number;
	best_game: { event: StatGame; points: number; goals: number } | null;
};
export type GoalieSeason = { season: Season; games: number; conceded: number; minutes: number | null; gaa: number | null; shutouts: number };
export type GoalieStats = {
	games: number;
	conceded: number;
	minutes: number | null;
	gaa: number | null;
	shutouts: number;
	seasons: GoalieSeason[];
	form: { event: StatGame; conceded: number; minutes: number | null; gaa: number | null }[];
};
export type MonthRate = { month: string; present: number; absent: number; rate: number | null };
export type KindRate = { kind: string; present: number; absent: number; rate: number | null };
export type AttendanceStats = { present: number; absent: number; rate: number | null; months: MonthRate[]; kinds: KindRate[]; current_streak: number; longest_streak: number };
export type OfficiatingKind = {
	games: number;
	games_this_season: number;
	games_per_month: number;
	penalties_per_game: number | null;
	fees: string;
	/** Referee / official types, most used first ('' = no type); `months[].counts` follow this order. */
	types: string[];
	months: { month: string; counts: number[] }[];
	seasons: { season: { id: string; name: string }; games: number }[];
	/** `competition` is the federation / source ('' = none), `teams` our team(s) in those games: one bucket per pair. */
	competitions: { competition: string; teams: string; games: number }[];
};
export type OfficiatingStats = { referee: OfficiatingKind | null; official: OfficiatingKind | null };
export type StaffLine = { season: Season; team: { id: string; name: string }; position: string; games: number; won: number; drawn: number; lost: number; gf: number; ga: number };
/** `update_in_progress`: game stats are being re-linked in the background after the player joined a team. */
export type MemberStats = { analytics_available: boolean; update_in_progress?: boolean; player: PlayerStats | null; goalie: GoalieStats | null; attendance: AttendanceStats | null; staff: StaffLine[]; officiating?: OfficiatingStats | null; share_token?: string | null };

const LOCALE: Record<string, string> = { en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' };
const locale = () => LOCALE[getLang()] ?? 'en-GB';

/** "2026-03" → "Mar" (short) or "March 2026" (long). */
export function monthLabel(month: string, long = false): string {
	const [y, m] = month.split('-').map(Number);
	const d = new Date(y, (m || 1) - 1, 1);
	return new Intl.DateTimeFormat(locale(), long ? { month: 'long', year: 'numeric' } : { month: 'short' }).format(d);
}

export const seasonLabel = (s: Season) => s?.name ?? '–';
/** Whether any section of the stats has something to chart. */
export const hasStats = (s: MemberStats | null | undefined) => !!s && !!(s.player || s.goalie || s.attendance || s.staff.length || s.update_in_progress);
