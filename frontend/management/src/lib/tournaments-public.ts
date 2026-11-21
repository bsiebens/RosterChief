/** The public tournament pages (`/tournaments/...`) and the member volunteer shifts: shapes of GET /public/tournaments and /portal/tournaments. */
import { getLang } from '#lib/i18n.svelte.ts';

export type TournamentStatus = 'draft' | 'published' | 'in_progress' | 'completed' | 'cancelled';
export type LunchMode = 'none' | 'optional' | 'mandatory';
export type TournamentItem = { slug: string; name: string; start_date: string; end_date: string; status: TournamentStatus; location: string | null; accepts_registrations: boolean };
export type TournamentList = { upcoming: TournamentItem[]; past: TournamentItem[] };

export type TeamRef = { id: string; name: string; slug: string; logo_url: string | null; primary_color: string; secondary_color: string };
export type StageSummary = { id: string; name: string; sequence: number; format: string; status: string; rounds: number | null; num_groups: number | null; points_win: number; points_draw: number; points_loss: number };
export type PublicMatch = {
	id: string;
	stage_id: string;
	group: { id: string; name: string } | null;
	round_number: number;
	round_label: string;
	sequence: number;
	field: { id: string; name: string } | null;
	scheduled_start: string | null;
	status: string;
	score_home: number | null;
	score_away: number | null;
	went_to_overtime: boolean;
	penalty_home: number | null;
	penalty_away: number | null;
	home_team: TeamRef | null;
	away_team: TeamRef | null;
	home_display: string;
	away_display: string;
};
export type Round = { round_number: number; label: string; all_complete: boolean; matches: PublicMatch[] };
export type StandingRow = { rank: number; team: TeamRef; played: number; won: number; drawn: number; lost: number; points: number; goals_for: number; goals_against: number; goal_diff: number };
export type StandingTable = { group: { id: string; name: string } | null; rows: StandingRow[] };
export type StageStandings = { stage: StageSummary; tables: StandingTable[] };
export type Bracket = { stage: StageSummary; rounds: Round[] };

export type TournamentDetail = TournamentItem & {
	description_html: string;
	contact_email: string;
	min_age: number | null;
	max_age: number | null;
	entry_fee_amount: string | null;
	deposit_amount: string | null;
	lunch_mode: LunchMode;
	lunch_price_per_day: string | null;
	dates: string[];
	registration_open: boolean;
	stages: StageSummary[];
	upcoming_matches: PublicMatch[];
	standings: StageStandings[];
	brackets: Bracket[];
};
export type PublicSchedule = { tournament: TournamentItem; stages: { stage: StageSummary; rounds: Round[] }[] };
export type PublicStandings = { tournament: TournamentItem; standings: StageStandings[]; brackets: Bracket[] };
export type PublicTeams = { tournament: TournamentItem; teams: (TeamRef & { dressing_rooms: string[] })[] };
export type PublicTeam = {
	tournament: TournamentItem;
	team: TeamRef & { withdrawn: boolean };
	roster: { name: string; role: 'player' | 'staff'; position_label: string; jersey_number: number | null }[];
	dressing_rooms: { name: string; assigned_from: string | null; assigned_until: string | null }[];
	matches: PublicMatch[];
};

export type PlayerRow = {
	first_name: string;
	last_name: string;
	jersey_number: number | null;
	role: 'player' | 'staff';
	position_label: string;
	date_of_birth: string | null;
	license_number: string;
	dietary_restrictions: string;
	lunch_days: string[];
	age_flagged?: boolean;
};
export type RegistrationStatus = {
	tournament: TournamentItem & { contact_email: string; lunch_mode: LunchMode; lunch_price_per_day: string | null; dates: string[]; min_age: number | null; max_age: number | null };
	registration: { status: 'pending' | 'approved' | 'rejected'; name: string; contact_name: string; contact_email: string; contact_phone: string; primary_color: string; secondary_color: string; logo_url: string | null; rejection_reason: string; submitted_at: string };
	team: { slug: string; withdrawn_at: string | null; withdrawal_reason: string; payment_status: 'unpaid' | 'deposit_received' | 'fully_paid' } | null;
	players: PlayerRow[];
	players_editable: boolean;
	roster_closed: boolean;
	lunch_is_mandatory: boolean;
	mandatory_total: string | null;
	lunch_total: string | null;
	amount_owed: string | null;
};

export type PortalShiftTournament = { slug: string; name: string; start_date: string; end_date: string; shift_count: number };
export type PortalShift = { id: string; title: string; description: string; starts_at: string; ends_at: string; location_label: string; needed: number; claimed: number; open_slots: number; is_full: boolean; claimed_by_me: boolean; claimed_by: string[] };

/** One editable person row of the registration form. Numbers and dates are kept as the input strings. */
export type PlayerDraft = { uid: number; first_name: string; last_name: string; role: 'player' | 'staff'; jersey_number: string; position_label: string; date_of_birth: string; license_number: string; dietary_restrictions: string; lunch_days: string[] };
let counter = 0;
export const blankPlayer = (role: 'player' | 'staff' = 'player', lunchDays: string[] = []): PlayerDraft => ({ uid: ++counter, first_name: '', last_name: '', role, jersey_number: '', position_label: '', date_of_birth: '', license_number: '', dietary_restrictions: '', lunch_days: lunchDays });
export const draftFrom = (p: PlayerRow): PlayerDraft => ({ uid: ++counter, first_name: p.first_name, last_name: p.last_name, role: p.role, jersey_number: p.jersey_number === null ? '' : String(p.jersey_number), position_label: p.position_label ?? '', date_of_birth: p.date_of_birth ?? '', license_number: p.license_number ?? '', dietary_restrictions: p.dietary_restrictions ?? '', lunch_days: [...(p.lunch_days ?? [])] });
/** A row as the API takes it. Every row is sent (blank ones too) so that error keys `players.N.*` line up with the form. */
export const playerBody = (p: PlayerDraft) => ({
	first_name: p.first_name.trim(),
	last_name: p.last_name.trim(),
	jersey_number: p.jersey_number.trim() === '' ? null : Number(p.jersey_number),
	role: p.role,
	position_label: p.position_label.trim(),
	date_of_birth: p.role === 'staff' || !p.date_of_birth ? null : p.date_of_birth,
	license_number: p.license_number.trim(),
	dietary_restrictions: p.dietary_restrictions.trim(),
	lunch_days: p.lunch_days
});
export const isBlank = (p: PlayerDraft) => !p.first_name.trim() && !p.last_name.trim();

/** Age on the first day of the tournament, the same rule the backend flags with (whole years). */
export function ageOn(dob: string, start: string): number | null {
	const b = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dob);
	const s = /^(\d{4})-(\d{2})-(\d{2})$/.exec(start);
	if (!b || !s) return null;
	const before = Number(s[2]) < Number(b[2]) || (Number(s[2]) === Number(b[2]) && Number(s[3]) < Number(b[3]));
	return Number(s[1]) - Number(b[1]) - (before ? 1 : 0);
}
export const outOfRange = (dob: string, start: string, min: number | null, max: number | null) => {
	const age = ageOn(dob, start);
	return age !== null && ((min !== null && age < min) || (max !== null && age > max));
};

/** Dates are YYYY-MM-DD (shown without a time zone shift) or ISO datetimes. */
const parse = (iso: string) => (/^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso));
export const dayLabel = (iso: string, style: 'long' | 'short' = 'long') => new Intl.DateTimeFormat(getLang(), style === 'long' ? { dateStyle: 'long' } : { weekday: 'short', day: 'numeric', month: 'short' }).format(parse(iso));
export const timeLabel = (iso: string) => new Intl.DateTimeFormat(getLang(), { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
export const dateTimeLabel = (iso: string) => new Intl.DateTimeFormat(getLang(), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
export const range = (a: string, b: string) => (a === b ? dayLabel(a) : `${dayLabel(a, 'short')} – ${dayLabel(b)}`);
