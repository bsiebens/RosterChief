import { getLang } from '#lib/i18n.svelte.ts';

/** Types and helpers of the referee management page (`/api/v1/referee-management`, see api/schemas_referees.py). */
export type RefKind = 'referee' | 'official';
export const REF_KINDS: RefKind[] = ['referee', 'official'];

export type Conflict = { event_id: string; title: string; start: string; end: string; kind: string; role: 'attendance' | 'referee' | 'official' };
export type Signup = { id: string; kind: RefKind; member_id: string; name: string; created: string; can_assign: boolean; conflicts: Conflict[] };

export type Assignment = {
	id: string;
	member_id: string | null;
	name: string;
	is_external: boolean;
	type_id: string | null;
	type_name: string;
	fee: string;
	km: string | null;
	km_rate: string | null;
	no_fee: boolean;
	fee_pending: boolean;
	total: string;
	conflicts: Conflict[];
};

export type Game = {
	id: string;
	title: string;
	kind: string;
	start: string;
	end: string;
	location_name: string;
	opponent_name: string;
	teams: { id: string; name: string; short_name: string }[];
	referees: Assignment[];
	officials: Assignment[];
	max_referees: number;
	max_officials: number;
	needs_referees: boolean;
	needs_officials: boolean;
	referee_short: boolean;
	official_short: boolean;
	signups: Signup[];
	fees_pending: boolean;
	conflict_count: number;
	total: string;
};

export type GamesOut = { games: Game[]; counts: Record<string, number>; range: string; need: string; scope: string };
export type Kpi = { games: number; none: number; partial: number; full: number };
export type Workload = { key: string; member_id: string | null; name: string; kind: RefKind; games: number; total: string };
export type TypeRef = { id: string; name: string };
export type Summary = { season_name: string; kpis: Record<RefKind, Kpi>; fees_pending: number; workload: Workload[]; types: Record<RefKind, TypeRef[]> };
export type Candidate = { id: string; name: string; level: string; expired: boolean; eligible: boolean; signed_up: boolean; conflicts: Conflict[] };

export const RANGES = ['week', 'two_weeks', '10', '25', '50'] as const;
export const NEEDS = ['all', 'open', 'referees', 'officials', 'fees'] as const;
export const TABS = ['games', 'payments', 'workload'] as const;
export type Tab = (typeof TABS)[number];

const LOCALE: Record<string, string> = { en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' };
const locale = () => LOCALE[getLang()] ?? 'en-GB';

export const eur = (value: string | number | null | undefined) => new Intl.NumberFormat(locale(), { style: 'currency', currency: 'EUR' }).format(Number(value ?? 0));
export const fmtDay = (iso: string) => new Intl.DateTimeFormat(locale(), { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(iso));
export const fmtTime = (iso: string) => new Intl.DateTimeFormat(locale(), { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));

/** The game's title line: "U12 vs Opponent", falling back to the stored title. */
export const gameLabel = (g: Pick<Game, 'teams' | 'opponent_name' | 'title'>) => {
	const teams = g.teams.map((t) => t.short_name || t.name).join(', ');
	if (teams && g.opponent_name) return `${teams} · ${g.opponent_name}`;
	return teams || g.title;
};

/** "Sat 7 Nov, 14:00-16:00" for a conflict chip. */
export const fmtRange = (c: { start: string; end: string }) => `${fmtDay(c.start)} ${fmtTime(c.start)}-${fmtTime(c.end)}`;

/** Column template of an assignment row and its header (name, type, no fee, fee, km, per km, total, actions). */
export const ROW_GRID = 'lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_5.5rem_5rem_4rem_4.5rem_4.5rem_9.5rem]';
