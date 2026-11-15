/**
 * Calendar types (mirror of api/schemas_events.py) and small helpers shared by the calendar, the event page and the
 * event form. Datetimes are ISO strings as the API sends them; dates are YYYY-MM-DD.
 */
import { getLang, t } from '#lib/i18n.svelte.ts';

export type Ref = { id: string; name: string };
/** `is_home`: one of the club's own venues (a game there is a home game). */
export type LocationRef = { id: string; name: string; city: string; address?: string; is_home?: boolean };
/** A team as the options offer it; `short_name` is what a game title uses when the API sends it (it does not yet). */
export type TeamRef = Ref & { short_name?: string };
export type OpponentRef = { id: string; name: string; logo_url: string | null };
export type Choice = { value: string; label: string };

export const EVENT_KINDS = ['training', 'game', 'tournament', 'meeting', 'social', 'other'] as const;
export type EventKind = (typeof EVENT_KINDS)[number];

export const ATTENDANCE_STATUSES = ['present', 'absent', 'excused', 'maybe', 'no_response', 'selected', 'not_selected'] as const;
export type AttendanceStatus = (typeof ATTENDANCE_STATUSES)[number];
/** The answers a coach may set (PUT /events/{id}/attendance/{member_id}, POST .../bulk). */
export const COACH_ANSWERS = ['present', 'absent', 'maybe', 'no_response'] as const;
export type CoachAnswer = (typeof COACH_ANSWERS)[number];

export type AttendanceCounts = {
	present: number;
	absent: number;
	excused: number;
	maybe: number;
	no_response: number;
	selected: number;
	not_selected: number;
	/** Invited (attendance rows). */
	total: number;
};

/** GET /events?from=&to= (one row per event). */
export type EventRow = {
	id: string;
	title: string;
	kind: EventKind | string;
	subtype_id: string | null;
	subtype_name: string;
	start: string;
	end: string | null;
	gathering: string | null;
	deadline: string | null;
	cancelled: boolean;
	location: LocationRef | null;
	opponent: OpponentRef | null;
	teams: Ref[];
	groups: Ref[];
	club_wide: boolean;
	series_id: string | null;
	detached: boolean;
	is_friendly: boolean;
	score_for: number | null;
	score_against: number | null;
	is_home_game: boolean;
	/** A competition game id exists: the score and game sheet can be fetched. */
	has_game_source: boolean;
	attendance: AttendanceCounts;
	can_edit: boolean;
	can_manage_attendance: boolean;
};

export type RecurrenceValues = {
	frequency: string | null;
	interval: number | null;
	/** Weekday codes: MO TU WE TH FR SA SU. */
	weekdays: string[];
	count: number | null;
	until: string | null;
	rrule: string;
	friendly: boolean;
};

export type SeriesInfo = { id: string; title: string; rrule: string; description: string; dtstart: string; until: string | null; recurrence: RecurrenceValues };

export type LineupStatus = 'none' | 'draft' | 'scheduled' | 'published';
export type LineupInfo = { available: boolean; status: LineupStatus; team_id: string | null; published_at: string | null; scheduled_publish_at: string | null };

/** GET /events/{id} */
/** A referee or official on the game (member links to their profile; no fees). */
export type Officiating = { name: string; member_id: string | null; type_name: string; is_external: boolean };
export type EventDetail = EventRow & {
	season: Ref | null;
	invited_member_ids: string[];
	excluded_member_ids: string[];
	invited_members: Ref[];
	excluded_members: Ref[];
	series: SeriesInfo | null;
	lineup: LineupInfo;
	competition: string;
	competition_label: string;
	score_for: number | null;
	score_against: number | null;
	is_live: boolean;
	is_home_game: boolean;
	created_by: Ref | null;
	task_count: number;
	attachment_count: number;
	has_game_sheet: boolean;
	referees: Officiating[];
	officials: Officiating[];
};

export type WeekdayChoice = { code: string; label: string };

/** GET /events/options */
export type EventOptions = {
	can_create: boolean;
	teams: TeamRef[];
	groups: Ref[];
	/** Every team / group (the calendar filters); `teams` / `groups` are only what the user may schedule for. */
	filter_teams?: TeamRef[];
	filter_groups?: Ref[];
	can_club_wide: boolean;
	locations: LocationRef[];
	opponents: OpponentRef[];
	kinds: Choice[];
	subtypes: EventSubtypeRef[];
	frequencies: string[];
	weekdays: WeekdayChoice[];
};

/** An event subtype as the form offers it; `title_template` may use {teams}, {opponent} and {kind}. */
export type EventSubtypeRef = { id: string; kind: string; name: string; title_template: string };

export type RecurrenceIn = { frequency: string; interval?: number; weekdays?: string[]; until?: string | null; count?: number | null };

/** POST /events */
export type EventIn = {
	title?: string;
	kind?: string;
	subtype_id?: string | null;
	start: string;
	end?: string | null;
	gathering?: string | null;
	deadline?: string | null;
	location_id?: string | null;
	opponent_id?: string | null;
	team_ids?: string[];
	group_ids?: string[];
	club_wide?: boolean;
	invited_member_ids?: string[];
	excluded_member_ids?: string[];
	season_id?: string | null;
	is_friendly?: boolean;
	recurrence?: RecurrenceIn | null;
	rrule?: string | null;
	notify?: boolean;
};

/** PATCH /events/{id}?scope=this|following|all (only sent fields change). */
export type EventPatch = Partial<Omit<EventIn, 'start'>> & { start?: string | null; cancelled?: boolean | null };
export type EditScope = 'this' | 'following' | 'all';

export type EventValues = {
	title: string;
	kind: string;
	subtype_id: string | null;
	start: string | null;
	end: string | null;
	gathering: string | null;
	deadline: string | null;
	location_id: string | null;
	opponent_id: string | null;
	team_ids: string[];
	group_ids: string[];
	club_wide: boolean;
	invited_member_ids: string[];
	excluded_member_ids: string[];
	is_friendly: boolean;
	cancelled: boolean;
	recurrence: RecurrenceValues | null;
};

/** GET /events/{id}/form */
export type EventForm = { event: EventValues; series: EventValues | null; series_description: string; can_edit: boolean };

// -- attendance --

export type AttendanceRow = {
	member: Ref;
	first_name: string;
	last_name: string;
	status: AttendanceStatus | string;
	showed_up: boolean | null;
	note: string;
	/** Only for people who manage this event's attendance. */
	coach_note: string | null;
	answered_by_coach: boolean;
	/** Who set the current answer (null for a reset or when nobody did) and how. */
	answered_by_name?: string | null;
	answered_via?: 'coach' | 'self' | 'guardian' | 'system' | null;
	coach_set_at: string | null;
	position: string;
	jersey_number: number | null;
	team_id: string | null;
	selected: boolean;
	has_user: boolean;
};

/** GET /events/{id}/attendance */
export type AttendanceList = { can_manage: boolean; counts: AttendanceCounts; checked_in: number; no_shows: number; rows: AttendanceRow[]; /** People who would be invited but cannot join because of open requirements (attendance managers only). */ blocked?: { member_id: string; first_name: string; last_name: string; requirements: string[] }[] };
export type BulkResult = { member_id: string; ok: boolean; status: string | null; error: string };
export type BulkAnswerOut = { results: BulkResult[] };

// -- line-up --

export type LineupMember = { member: Ref; position: string; jersey_number: number | null; status: string; available: boolean; on_roster: boolean; selected: boolean };
/** GET /events/{id}/lineup */
export type Lineup = {
	available: boolean;
	can_manage: boolean;
	status: LineupStatus;
	team: Ref | null;
	teams: Ref[];
	published_at: string | null;
	scheduled_publish_at: string | null;
	members: LineupMember[];
};
export type LineupPublishOut = { lineup: Lineup; newly_selected: string[]; dropped: string[] };

// -- tasks --

export type Claim = { id: string; label: string; member_id: string | null; member_name: string | null };
export type Task = { id: string; title: string; description: string; needed_quantity: number; claimed_count: number; open_slots: number; claimed_by_me: boolean; claims: Claim[]; created_by: Ref | null };
/** GET /events/{id}/tasks */
export type TaskList = { can_manage: boolean; tasks: Task[] };

// -- attachments --

export type Attachment = { id: string; name: string; size: number; uploaded_by: Ref | null; created: string; download_url: string };
/** GET /events/{id}/attachments */
export type GameSheetRow = { time: string; type: string; ours: boolean | null; team: string; icon: string; kind: string; who: string; detail: string; tag: string; score: string; serious: boolean };
export type LineupPlayer = { pos: string; number: number | null; name: string; member_id?: string | null; photo_url?: string | null; initials: string; goals?: number; assists?: number; penalty_minutes?: number };
export type GameLineup = { ours_label: string; theirs_label: string; ours: LineupPlayer[]; theirs: LineupPlayer[] };
export type GameSheet = {
	status: string;
	final: boolean;
	updated_at: string | null;
	source: string;
	ours_label: string;
	theirs_label: string;
	score: string;
	period_scores: { label: string; score: string }[];
	periods: { label: string; title: string; score: string; rows: GameSheetRow[] }[];
	lineup: GameLineup | null;
};

export type AttachmentList = { can_manage: boolean; attachments: Attachment[] };

// -- helpers --

export const eventHref = (id: string) => `/calendar/events/${id}`;
export const eventEditHref = (id: string) => `/calendar/events/${id}/edit`;

/** i18n key of a kind's label (messages/calendar-events.ts). */
export const kindLabelKey = (kind: string) => `events.kind.${(EVENT_KINDS as readonly string[]).includes(kind) ? kind : 'other'}`;
/** i18n key of an attendance status label. */
export const statusLabelKey = (status: string) => `events.status.${status}`;

/**
 * Tailwind classes per kind, built from the theme tokens (club accent, primary, ice, ok, warn, info). `dot` is a
 * small swatch / left border, `chip` a soft badge, `bar` the left border of a card.
 */
export const KIND_STYLE: Record<EventKind, { dot: string; chip: string; bar: string }> = {
	training: { dot: 'bg-ice', chip: 'bg-info-bg text-info-text border-info-border', bar: 'border-l-ice' },
	game: { dot: 'bg-club', chip: 'bg-club text-club-content border-club', bar: 'border-l-club' },
	tournament: { dot: 'bg-club-dark', chip: 'bg-club-dark text-club-content border-club-dark', bar: 'border-l-club-dark' },
	meeting: { dot: 'bg-primary', chip: 'bg-primary text-primary-content border-primary', bar: 'border-l-primary' },
	social: { dot: 'bg-ok', chip: 'bg-ok-bg text-ok-text border-ok-border', bar: 'border-l-ok' },
	other: { dot: 'bg-dim', chip: 'bg-subhead text-slate border-line', bar: 'border-l-dim' }
};
/** "5 – 2" (our score first) for a played game with a score, else ''. */
export const scoreText = (e: { score_for: number | null; score_against: number | null }) => (e.score_for === null || e.score_against === null ? '' : `${e.score_for} – ${e.score_against}`);
/** Badge colours of a result: won / drawn / lost for us. */
export const resultStyle = (e: { score_for: number | null; score_against: number | null }) =>
	e.score_for === null || e.score_against === null ? '' : e.score_for > e.score_against ? 'bg-ok-bg text-ok-text border-ok-border' : e.score_for < e.score_against ? 'bg-danger-bg text-danger border-danger-border' : 'bg-subhead text-muted border-line';

export const kindStyle = (kind: string) => KIND_STYLE[(kind as EventKind) in KIND_STYLE ? (kind as EventKind) : 'other'];

/** Pill classes per attendance status. */
export const STATUS_STYLE: Record<string, string> = {
	present: 'bg-ok-bg text-ok-text border-ok-border',
	selected: 'bg-ok-bg text-ok-text border-ok-border',
	absent: 'bg-danger-bg text-danger border-danger-border',
	excused: 'bg-danger-bg text-danger border-danger-border',
	not_selected: 'bg-subhead text-muted border-line',
	maybe: 'bg-warn-bg text-warn-text border-warn-border',
	no_response: 'bg-subhead text-muted border-line'
};
export const statusStyle = (status: string) => STATUS_STYLE[status] ?? STATUS_STYLE.no_response;

const locale = () => ({ en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' })[getLang()] ?? 'en-GB';

const fmt = (iso: string | Date, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(locale(), opts).format(typeof iso === 'string' ? new Date(iso) : iso);

/** 18:30 */
export const formatTime = (iso: string | Date) => fmt(iso, { hour: '2-digit', minute: '2-digit' });
/** Mon 3 Nov 2026 */
export const formatDay = (iso: string | Date) => fmt(iso, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
/** Monday 3 November */
export const formatLongDay = (iso: string | Date) => fmt(iso, { weekday: 'long', day: 'numeric', month: 'long' });
/** Mon 3 Nov 2026, 18:30 */
export const formatDateTime = (iso: string | Date) => fmt(iso, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
/** November 2026 */
export const formatMonth = (d: Date) => fmt(d, { month: 'long', year: 'numeric' });
/** Mon (short weekday name). */
export const formatWeekday = (d: Date, style: 'short' | 'narrow' | 'long' = 'short') => fmt(d, { weekday: style });

/** Local YYYY-MM-DD of a date. */
export const isoDate = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
/** Parse YYYY-MM-DD as a local date (null when invalid). */
export function parseIsoDate(s: string | null | undefined): Date | null {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s ?? '');
	if (!m) return null;
	const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
	return Number.isNaN(d.getTime()) ? null : d;
}
export const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
/** Monday of the week that holds `d`. */
export const weekStart = (d: Date) => addDays(d, -((d.getDay() + 6) % 7));
/** How many days the calendar's agenda view shows from its start date. */
export const AGENDA_DAYS = 28;
export const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** "18:30 – 20:00" on one day, else "Mon 3 Nov 2026, 18:30 – Tue 4 Nov 2026, 10:00". */
export function formatRange(start: string, end: string | null): string {
	if (!end) return `${formatDay(start)}, ${formatTime(start)}`;
	const s = new Date(start);
	const e = new Date(end);
	if (sameDay(s, e)) return `${formatDay(start)}, ${formatTime(start)} – ${formatTime(end)}`;
	return `${formatDateTime(start)} – ${formatDateTime(end)}`;
}

/** Human file size (1.2 MB). */
export function formatSize(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} kB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Value for <input type="datetime-local"> from an ISO datetime (local time). */
export function toLocalInput(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	return `${isoDate(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

const WEEKDAY_INDEX: Record<string, number> = { MO: 0, TU: 1, WE: 2, TH: 3, FR: 4, SA: 5, SU: 6 };
/** Localised short weekday name of an RRULE code (MO .. SU). */
export const weekdayCodeLabel = (code: string, style: 'short' | 'long' = 'short') => formatWeekday(new Date(2024, 0, 1 + (WEEKDAY_INDEX[code] ?? 0)), style);

/**
 * "Every week on Mon, Wed until 30 Jun 2027" in the current language, from a series' recurrence values; `fallback`
 * (the backend's own description) for raw rules the picker cannot show.
 */
export function describeRecurrence(r: RecurrenceValues | null | undefined, fallback = ''): string {
	if (!r || !r.friendly || !r.frequency) return fallback;
	const n = r.interval ?? 1;
	let out = n === 1 ? t(`events.rec.every.${r.frequency}`) : t(`events.rec.every_n.${r.frequency}`, { n });
	if (r.frequency === 'weekly' && r.weekdays.length) out += ` ${t('events.rec.on', { days: [...r.weekdays].sort((a, b) => (WEEKDAY_INDEX[a] ?? 0) - (WEEKDAY_INDEX[b] ?? 0)).map((c) => weekdayCodeLabel(c)).join(', ') })}`;
	if (r.until) out += ` ${t('events.rec.until', { date: formatDay(parseIsoDate(r.until) ?? r.until) })}`;
	else if (r.count) out += `, ${t('events.rec.times', { n: r.count })}`;
	return out;
}

/** One logged change of an answer, newest first from the history endpoint. */
export type AttendanceChange = {
	id: string;
	created: string;
	from_status: string;
	to_status: string;
	changed_by_name: string;
	via: 'coach' | 'self' | 'guardian' | 'system' | string;
	note_changed: boolean;
};
