/**
 * Game analytics as the API returns them (api/schemas_analytics.py): `GET /teams/{id}/analytics` (TeamAnalytics) and
 * `GET /members/{id}/game-stats` (MemberGameStats). Both answer 404 when the club has no analytics (not a hockey club,
 * or the `analytics` feature is off); the team detail's `analytics_available` says so up front.
 *
 * `*_pct` fields are 0–100 shares of the largest value of their chart, computed by the server.
 */
import { getLang } from '#lib/i18n.svelte.ts';

export const SCOPES = ['all', 'home', 'away', 'last5'] as const;
export type Scope = (typeof SCOPES)[number];
export type Result = 'W' | 'D' | 'L';

export type GameRef = {
	id: string;
	title: string;
	start: string;
	opponent: string | null;
	is_home_game: boolean;
	competition: string;
	is_friendly: boolean;
	score_for: number | null;
	score_against: number | null;
};
export type Leader = { name: string; number: number | null; member_id: string | null; initials: string; total: number; goals: number; pct: number };
export type Goalie = Leader & { seconds: number; minutes: number | null; gaa: number | null };
export type ForAgainst = { label: string; goals_for: number; goals_against: number; for_pct: number; against_pct: number };
export type SplitRow = { games: number; won: number; drawn: number; lost: number; gf: number; ga: number; diff: number; avg_for: number | null; avg_against: number | null; share_for: number };
export type OpponentRow = { name: string; games: number; won: number; drawn: number; lost: number; gf: number; ga: number };
export type GameRow = { event: GameRef; result: Result; goals_for: number; goals_against: number; pim_for: number | null; pim_against: number | null; pim_for_pct: number; pim_against_pct: number; total_pct: number };
export type TrendPoint = { x: number; y: number; value: number; game: GameRef | null };
export type Trend = { points: TrendPoint[]; line: string; area: string; zero_y: number; last: number };
export type PenaltyRef = { time: string; player: string; kind: string; side: string; ours: boolean };
export type StrengthWarning = {
	event: GameRef;
	goal: string;
	ours: boolean;
	player: string;
	period: string;
	time: string;
	recorded: string;
	recorded_label: string;
	keep: string;
	expected: string;
	expected_label: string;
	penalties: PenaltyRef[];
	can_fix: boolean;
};

export type TeamAnalytics = {
	team: { id: string; name: string };
	season: { id: string; name: string };
	scope: Scope;
	friendlies: boolean;
	competition: string;
	competitions: string[];
	capabilities: { analytics: boolean };
	games: number;
	timeline_games: number;
	record: { won: number; drawn: number; lost: number };
	points_table: number;
	goals_for: number;
	goals_against: number;
	goal_difference: number;
	avg_for: number | null;
	avg_against: number | null;
	clean_sheets: number;
	form: { result: Result; event: GameRef }[];
	split: { home: SplitRow; away: SplitRow };
	pim_for: number;
	pim_against: number;
	avg_pim: number | null;
	game_misconduct_minutes: number;
	avg_pim_excl_game_misconducts: number | null;
	pp_goals_for: number;
	pp_goals_against: number;
	pp_opportunities: number;
	pk_opportunities: number;
	pk_killed: number;
	pp_pct: number | null;
	pk_pct: number | null;
	game_winning_goals: number;
	empty_net_goals: number;
	shots_for: number | null;
	shots_against: number | null;
	shot_games: number;
	shooting_pct: number | null;
	save_pct: number | null;
	top_scorers: Leader[];
	top_assists: Leader[];
	top_points: Leader[];
	top_offenders: Leader[];
	goalies: Goalie[];
	goalie_unattributed: number;
	periods: ForAgainst[];
	goal_minutes: ForAgainst[];
	busiest_minutes: ForAgainst | null;
	opponents: OpponentRow[];
	game_rows: GameRow[];
	game_bars: { event: GameRef; result: Result; goals_for: number; goals_against: number; for_pct: number; against_pct: number }[];
	trend: Trend | null;
	strength_warnings: StrengthWarning[];
};

export type GoalieTotals = { games: number; conceded: number; seconds: number; minutes: number | null; gaa: number | null };
export type StatCounters = { games: number; goals: number; assists: number; points: number; penalty_minutes: number; pp_goals: number; game_winning_goals: number; goalie: GoalieTotals | null };
export type MemberGameStats = StatCounters & { season: { id: string; name: string } | null; teams: (StatCounters & { team: { id: string; name: string }; season: { id: string; name: string } | null })[] };

/** Strengths a goal can be corrected to (events/services/strength_check.py STRENGTHS). */
export const STRENGTHS = ['EQ', 'PP1', 'PP2', 'SH1', 'SH2'] as const;

const LOCALE: Record<string, string> = { en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' };
const locale = () => LOCALE[getLang()] ?? 'en-GB';

/** 3 Nov */
export const shortDate = (iso: string) => {
	const d = new Date(iso);
	return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat(locale(), { day: 'numeric', month: 'short' }).format(d);
};
/** 2.35 / 90.5 % with the language's decimal mark; null → "—". */
export const num = (value: number | null | undefined, digits = 1) => (value === null || value === undefined ? '—' : new Intl.NumberFormat(locale(), { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value));
export const pct = (value: number | null | undefined) => (value === null || value === undefined ? '—' : new Intl.NumberFormat(locale(), { style: 'percent', maximumFractionDigits: 1 }).format(value / 100));
/** +3 / 0 / −2 */
export const signed = (value: number) => (value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : '0');

/** Chip classes per result: a win in the ok tone, a loss in the danger tone, a draw neutral. */
export const RESULT_CHIP: Record<Result, string> = {
	W: 'bg-ok-bg text-ok-text border-ok-border',
	D: 'bg-subhead text-slate border-line',
	L: 'bg-danger-bg text-danger border-danger-border'
};

/** The analytics page of a team, keeping the season. */
export const analyticsHref = (teamId: string, season?: string | null) => `/teams/${teamId}/analytics${season ? `?season=${season}` : ''}`;
