import { invalidateAll } from '$app/navigation';
import { api, type ApiCall } from '#lib/management-api.ts';
import { t } from '#lib/i18n.svelte.ts';

/** Management pages live here (the public and member-portal tournament pages own `/tournaments`). */
export const TBASE = '/tournaments-admin';

export type TTeamRef = { id: string; name: string; slug?: string; logo_url?: string | null; primary_color?: string; secondary_color?: string };
export type Season = { id: string; name: string };
export type TournamentItem = {
	id: string;
	slug: string;
	name: string;
	season: Season;
	start_date: string;
	end_date: string;
	status: 'draft' | 'published' | 'in_progress' | 'completed' | 'cancelled';
	registration_open: boolean;
	max_teams: number | null;
	accepts_registrations: boolean;
	pending_registration_count: number;
	teams_in_count: number;
};
/** One day the tournament is played on, with its opening hours (HH:MM). */
export type TDay = { date: string; starts_at: string; ends_at: string };
export type TournamentDetail = TournamentItem & {
	days: TDay[];
	is_full: boolean;
	contact_email: string;
	description: string;
	earliest_birth_year: number | null;
	latest_birth_year: number | null;
	home_team: { id: string; name: string } | null;
	event_id: string | null;
	deposit_amount: string | null;
	entry_fee_amount: string | null;
	lunch_mode: 'none' | 'optional' | 'mandatory';
	lunch_price_per_day: string | null;
	lunch_billed_with_fee: boolean;
	default_match_duration_minutes: number | null;
	default_gap_minutes: number;
	clock_direction: 'up' | 'down';
	ice_hockey: boolean;
	public_path: string;
	schedule_needs_regeneration: boolean;
	dates: string[];
	location: string | null;
	can_manage: boolean;
	can_operate: boolean;
};
export type Person = {
	id: string;
	member_id: string | null;
	name: string;
	first_name: string;
	last_name: string;
	jersey_number: number | null;
	role: 'player' | 'staff';
	position_label: string;
	date_of_birth: string | null;
	license_number: string;
	dietary_restrictions: string;
	lunch_days: string[];
	age_flagged: boolean;
};
export type Registration = {
	id: string;
	status: 'pending' | 'approved' | 'rejected';
	name: string;
	club_team: string | null;
	is_foreign: boolean;
	contact_name?: string;
	contact_email?: string;
	contact_phone?: string;
	primary_color: string;
	secondary_color: string;
	logo_url: string | null;
	rejection_reason: string;
	submitted_at: string;
	reviewed_at: string | null;
	tournament_team_id: string | null;
	is_oldest_pending: boolean;
	players: Person[];
};
export type TeamDoc = {
	id: string;
	name: string;
	slug: string;
	logo_url: string | null;
	primary_color: string;
	secondary_color: string;
	club_team: { id: string; name: string } | null;
	is_foreign: boolean;
	is_home_team: boolean;
	event_id: string | null;
	contact_name: string;
	contact_email: string;
	contact_phone: string;
	seed: number | null;
	payment_status: 'unpaid' | 'deposit_received' | 'fully_paid';
	withdrawn_at: string | null;
	withdrawal_reason: string;
	registration_id: string | null;
	player_count: number;
	staff_count: number;
	has_age_flag: boolean;
};
export type MatchSource = { type: 'match'; match_id: string; is_winner: boolean } | { type: 'group'; group_id: string; rank: number } | null;
export type MatchDoc = {
	id: string;
	stage_id: string;
	group: { id: string; name: string } | null;
	round_number: number;
	round_label: string;
	sequence: number;
	field: { id: string; name: string } | null;
	scheduled_start: string | null;
	auto_scheduled: boolean;
	status: string;
	score_home: number | null;
	score_away: number | null;
	went_to_overtime: boolean;
	penalty_home: number | null;
	penalty_away: number | null;
	home_team: TTeamRef | null;
	away_team: TTeamRef | null;
	home_display: string;
	away_display: string;
	/** What feeds a side that has no team yet: the winner/loser of another match, the Nth of a group, or nothing. */
	home_source?: MatchSource;
	away_source?: MatchSource;
	/** Back-and-forth ties: 1 or 2 for the leg; `aggregate` is both legs added up once the first leg is in (on the second leg). */
	leg: 1 | 2 | null;
	tie_id: string | null;
	first_leg_id: string | null;
	home_goalie_id?: string | null;
	away_goalie_id?: string | null;
	aggregate: [number, number] | null;
	/** Per-match attribute totals (e.g. penalty minutes), keyed by attribute key. */
	attributes?: Record<string, { home: number; away: number }>;
	event_count?: number;
};
export type ResultAttribute = { id: string; name: string; key: string; source: 'manual' | 'timeline'; event_type: string | null; aggregate: string | null; position: number };
export type MatchEventType = 'goal' | 'penalty' | 'goalie_change' | 'timeout' | 'other';
export type RosterPerson = { id: string; name: string; number: number | null; position?: string; is_goalie?: boolean };
export type MatchEvent = {
	id: string;
	type: MatchEventType;
	side: 'home' | 'away' | null;
	minute: number | null;
	second: number | null;
	player: string;
	player_id: string | null;
	player_number: number | null;
	assists: RosterPerson[];
	penalty_code: string;
	penalty_name: string;
	penalty_length: string;
	goalie_in: RosterPerson | null;
	goalie_out: RosterPerson | null;
	note: string;
	value: number | null;
};
/** The data of the game timeline screen: the match, its events, the players of both teams and the ice hockey lists (empty for other sports). */
export type GameTimeline = {
	/** The periods of a game played back and forth (empty for a single game), with their scores. */
	legs: { id: string; leg: number; status: string; score_home: number | null; score_away: number | null }[];
	match: MatchDoc;
	events: MatchEvent[];
	sport: { slug: string; name: string } | null;
	ice_hockey: boolean;
	penalty_codes: { code: string; name: string }[];
	penalty_lengths: string[];
	rosters: { home: RosterPerson[]; away: RosterPerson[] };
};
/** One step of a stage's ranking chain: `criterion` is a built-in name or `attr:<key>` for a match statistic. */
export type Tiebreaker = { criterion: string; direction: 'most' | 'least' };
export type AttributeDoc = { id: string; name: string; key: string; source: 'manual' | 'timeline'; event_type: string | null; aggregate: 'count' | 'sum' | null; position: number };
export type StageDoc = {
	id: string;
	name: string;
	sequence: number;
	format: 'single_elim' | 'double_elim' | 'round_robin' | 'groups' | 'swiss' | 'custom';
	status: string;
	rounds: number | null;
	num_groups: number | null;
	points_win: number;
	points_draw: number;
	points_loss: number;
	tiebreakers: Tiebreaker[];
	match_count: number;
	groups: { id: string; name: string }[];
	needs_seed_choice: boolean;
	supports_placement: boolean;
	placement: boolean;
	allow_draws: boolean;
	penalty_shots: boolean;
	two_legged: boolean;
	match_duration_minutes: number | null;
	is_knockout: boolean;
};
export type FieldDoc = { id: string; name: string; availability: { date: string; start: string; end: string }[] };
export type BreakRule = {
	id: string;
	field_id: string | null;
	trigger_type: 'game_count' | 'elapsed_time' | 'round_boundary' | 'clock_window';
	trigger_every_n_games: number | null;
	trigger_every_n_minutes: number | null;
	trigger_after_round_number: number | null;
	trigger_window_start: string | null;
	trigger_window_end: string | null;
	gap_minutes: number | null;
	replaces_default_gap: boolean;
	priority: number | null;
	summary: string;
};
/** `starts_at`/`ends_at` are the whole reserved window (a lunch includes its padding, a planning constraint); a lunch's real sitting is `lunch_start`/`lunch_end`. */
export type ScheduleBlock = { id: string; kind?: 'block' | 'lunch'; title: string; starts_at: string; ends_at: string; lunch_start?: string | null; lunch_end?: string | null; location?: string; field: { id: string; name: string } | null; teams: TTeamRef[] };

export const STATUSES = ['draft', 'published', 'in_progress', 'completed', 'cancelled'] as const;
export const statusBadge = (s: string) => ({ draft: 'badge-ghost', published: 'badge-info', in_progress: 'badge-success', completed: 'badge-neutral', cancelled: 'badge-error' })[s] ?? 'badge-ghost';
export const FORMATS = ['single_elim', 'double_elim', 'round_robin', 'groups', 'swiss', 'custom'] as const;
export const TIEBREAKERS = ['goal_diff', 'goals_for', 'goals_against', 'wins', 'head_to_head'] as const;

/** What a block shows on the schedule: a lunch is just its sitting, the padding around it only constrains the planning. */
export const blockStart = (b: ScheduleBlock) => b.lunch_start ?? b.starts_at;
export const blockEnd = (b: ScheduleBlock) => b.lunch_end ?? b.ends_at;

/** Formats an ISO date (YYYY-MM-DD) in the user's language, without a time-zone shift. */
export function fmtDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short', year: 'numeric' }): string {
	if (!iso) return '—';
	const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
	return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, { ...opts, timeZone: 'UTC' });
}
export const fmtDay = (iso: string) => fmtDate(iso, { weekday: 'short', day: 'numeric', month: 'short' });
export const fmtDateRange = (a: string, b: string) => (a === b ? fmtDate(a) : `${fmtDate(a, { day: 'numeric', month: 'short' })} – ${fmtDate(b)}`);
export function fmtDateTime(iso: string | null | undefined): string {
	if (!iso) return '—';
	return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
export function fmtTime(iso: string | null | undefined): string {
	if (!iso) return '—';
	return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}
const pad = (n: number) => String(n).padStart(2, '0');
/** ISO datetime -> value for <input type="datetime-local"> in the browser's zone. */
export function toLocalInput(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
/** <input type="datetime-local"> value -> ISO 8601 with offset. */
export const fromLocalInput = (v: string): string => (v ? new Date(v).toISOString() : '');
export const numOrNull = (v: string | number | null | undefined): number | null => (v === '' || v === null || v === undefined ? null : Number(v));
export const strOrNull = (v: string | null | undefined): string | null => (v ? v : null);

type Done<T> = (data: T | undefined) => void | Promise<void>;
type Opts = { reload?: boolean; notice?: string };

/**
 * Runs mutating calls from the browser (through the same-origin proxy): tracks `busy`, the error message and the 422 field errors,
 * and reloads the page data after a success.
 */
export class Runner {
	busy = $state(false);
	error = $state('');
	fields = $state<Record<string, string>>({});
	notice = $state('');

	async run<T = any>(call: () => Promise<ApiCall<T>>, onOk?: Done<T>, opts: Opts = {}): Promise<boolean> {
		this.busy = true;
		this.error = '';
		this.fields = {};
		const res = await call();
		if (!res.ok) {
			this.busy = false;
			this.error = res.message || t(res.status === 0 ? 'common.error.network' : res.status === 403 ? 'common.error.forbidden' : 'common.error.generic');
			this.fields = res.fields;
			return false;
		}
		this.notice = opts.notice ?? '';
		await onOk?.(res.data);
		if (opts.reload !== false) await invalidateAll();
		this.busy = false;
		return true;
	}
	post<T = any>(path: string, body: unknown = {}, onOk?: Done<T>, opts?: Opts) {
		return this.run<T>(() => api<T>('POST', path, { body }), onOk, opts);
	}
	patch<T = any>(path: string, body: unknown, onOk?: Done<T>, opts?: Opts) {
		return this.run<T>(() => api<T>('PATCH', path, { body }), onOk, opts);
	}
	put<T = any>(path: string, body: unknown, onOk?: Done<T>, opts?: Opts) {
		return this.run<T>(() => api<T>('PUT', path, { body }), onOk, opts);
	}
	del(path: string, onOk?: Done<void>, opts?: Opts) {
		return this.run(() => api('DELETE', path), onOk, opts);
	}
	clear() {
		this.error = '';
		this.fields = {};
		this.notice = '';
	}
}

/**
 * The open tournament's status for the page header's pill. The `[id]` layout publishes it here from its own props: the header
 * renders while a navigation is tearing the route tree down, and reading `page.data` at that moment throws inside SvelteKit's root.
 */
export const tournamentHeader = $state<{ status: string }>({ status: '' });

/**
 * A plain copy of values a header snippet needs. The page header renders in the shared layout and can re-evaluate right after a
 * navigation removed the page's route node; reading the page's own props then throws inside SvelteKit's root (`n().data`).
 * The copy is refreshed while the page is alive and keeps its last value once the props are gone.
 */
export function headerSnapshot<T extends object>(get: () => T): T {
	const snap = $state(get());
	$effect(() => {
		try {
			Object.assign(snap, get());
		} catch {
			// The props are already gone; keep the last values until the header is cleared.
		}
	});
	return snap;
}

export type LunchSettings = { location?: string; duration_minutes: number; gap_before_minutes: number; gap_after_minutes: number; capacity_teams: number; interval_minutes: number };
export type LunchSitting = { start: string; end: string; teams: { id: string; name: string }[] };
export type LunchDay = { date: string; lunch_start: string | null; lunch_end: string | null; day_start: string; day_end: string; teams_eating: number; sittings: LunchSitting[] };
export type LunchDoc = { enabled: boolean; settings: LunchSettings; days: LunchDay[]; generated: boolean };
export type LunchProblem = { type: 'no_room' | 'game_close' | 'no_window'; date: string; team: { id: string; name: string } | null; message: string };
export type LunchResult = { sittings_created: number; games_replanned: number; problems: LunchProblem[]; note: string | null };
