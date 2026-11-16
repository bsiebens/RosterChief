/**
 * Add / edit event form: the form's state, how it is read from the API (GET /events/{id}/form) and turned into the
 * POST /events body or a PATCH /events/{id} diff, plus the local recurrence summary.
 *
 * Times: the API reads naive datetimes as club local time (Europe/Brussels) and sends aware ISO strings back. The form
 * works in wall-clock "YYYY-MM-DDTHH:MM" strings (the <input type="datetime-local"> value) and sends them as they are;
 * API values are converted into that wall time with the club time zone (not the browser's, and not the server's,
 * which may run in UTC during SSR).
 */
import { getLang, t } from '#lib/i18n.svelte.ts';
import type { EventIn, EventOptions, EventPatch, EventValues, RecurrenceIn, RecurrenceValues, Ref } from '#lib/events/types.ts';

export const CLUB_TZ = 'Europe/Brussels';

export type Repeat = 'none' | 'daily' | 'weekly' | 'monthly';
export type RepeatEnd = 'never' | 'until' | 'count';
export const REPEATS: Repeat[] = ['none', 'daily', 'weekly', 'monthly'];
export const WEEKDAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'] as const;

export type FormState = {
	kind: string;
	/** Id of the chosen event subtype ('' = none); always one of `kind`. */
	subtype_id: string;
	title: string;
	/** Wall-clock datetime-local values ("" = not set). */
	start: string;
	end: string;
	gathering: string;
	deadline: string;
	location_id: string;
	opponent_id: string;
	/** Notes for the players (plain text, at most NOTES_MAX characters). */
	notes: string;
	is_friendly: boolean;
	team_ids: string[];
	group_ids: string[];
	club_wide: boolean;
	invited: Ref[];
	excluded: Ref[];
	// recurrence
	repeat: Repeat;
	interval: number;
	weekdays: string[];
	ends: RepeatEnd;
	/** YYYY-MM-DD */
	until: string;
	count: number;
	/** Advanced: a raw RRULE; wins over the friendly fields when filled in. */
	rrule: string;
	notify: boolean;
};

export const NOTES_MAX = 2000;
export const isGame = (kind: string) => kind === 'game';
/** Kinds whose title the backend generates (from the subtype's template); the form shows it instead of a title field. */
export const generatesTitle = (kind: string) => kind === 'game' || kind === 'training';
/** A tournament without a title is called after its kind, so it needs none; every other kind does. */
export const titleOptional = (kind: string) => kind === 'tournament';

// -- time -------------------------------------------------------------------------------------------------------------

const pad = (n: number) => String(n).padStart(2, '0');

/** Aware ISO from the API -> club wall time "YYYY-MM-DDTHH:MM" for <input type="datetime-local">. */
export function isoToLocalInput(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	const parts = Object.fromEntries(
		new Intl.DateTimeFormat('en-GB', { timeZone: CLUB_TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
			.formatToParts(d)
			.map((p) => [p.type, p.value])
	);
	return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/** Today in the club's time zone, YYYY-MM-DD. */
export const clubToday = () => isoToLocalInput(new Date().toISOString()).slice(0, 10);
/** Now in the club's time zone, as a datetime-local value (for "is this in the past" checks). */
export const clubNow = () => isoToLocalInput(new Date().toISOString());

const LOCAL_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;

/** Minutes since the epoch of a wall-clock value, as if it were UTC: only for differences between wall times. */
function wallMinutes(local: string): number | null {
	const m = LOCAL_RE.exec(local);
	if (!m) return null;
	return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 60000;
}
function fromWallMinutes(min: number): string {
	const d = new Date(min * 60000);
	return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
/** A wall-clock value shifted by `minutes` (negative = earlier). "" when the input is not a datetime-local value. */
export function shiftLocal(local: string, minutes: number): string {
	const m = wallMinutes(local);
	return m === null ? '' : fromWallMinutes(m + minutes);
}
/**
 * The Monday before an event at a fixed time ("2026-11-02T18:00"): the latest Monday strictly before the event's date
 * (an event on a Monday gets the Monday a week earlier). "" when `start` is not a datetime-local value.
 */
export function mondayBefore(start: string, time = '18:00'): string {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(start);
	if (!m) return '';
	const js = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay(); // 0 = Sunday
	const sinceMonday = (js + 6) % 7; // Monday = 0
	const back = sinceMonday === 0 ? 7 : sinceMonday;
	return `${shiftLocal(`${m[1]}-${m[2]}-${m[3]}T00:00`, -back * 1440).slice(0, 10)}T${time}`;
}
/** b - a in minutes (null when either is missing). */
export function diffMinutes(a: string, b: string): number | null {
	const x = wallMinutes(a);
	const y = wallMinutes(b);
	return x === null || y === null ? null : y - x;
}
/** Whole days between the dates of two wall-clock values (b - a). */
export function diffDays(a: string, b: string): number {
	const x = wallMinutes(`${a.slice(0, 10)}T00:00`);
	const y = wallMinutes(`${b.slice(0, 10)}T00:00`);
	return x === null || y === null ? 0 : Math.round((y - x) / 1440);
}
/** Normalise to "YYYY-MM-DDTHH:MM" (drops seconds some browsers add). */
export const normLocal = (v: string) => (LOCAL_RE.test(v) ? v.slice(0, 16) : '');

// -- state <-> API ----------------------------------------------------------------------------------------------------

export function blankState(init: Partial<FormState> = {}): FormState {
	return {
		kind: 'training',
		subtype_id: '',
		title: '',
		start: '',
		end: '',
		gathering: '',
		deadline: '',
		location_id: '',
		opponent_id: '',
		notes: '',
		is_friendly: false,
		team_ids: [],
		group_ids: [],
		club_wide: false,
		invited: [],
		excluded: [],
		repeat: 'none',
		interval: 1,
		weekdays: [],
		ends: 'never',
		until: '',
		count: 10,
		rrule: '',
		notify: true,
		...init
	};
}

/** Recurrence fields of the form from the API's editing shape (``friendly`` false = only the raw rule can show it). */
export function recurrenceToState(r: RecurrenceValues | null | undefined, rrule = ''): Pick<FormState, 'repeat' | 'interval' | 'weekdays' | 'ends' | 'until' | 'count' | 'rrule'> {
	if (!r) return { repeat: 'none', interval: 1, weekdays: [], ends: 'never', until: '', count: 10, rrule: '' };
	const friendly = r.friendly && !!r.frequency;
	return {
		repeat: friendly ? ((r.frequency as Repeat) ?? 'weekly') : 'weekly',
		interval: r.interval ?? 1,
		weekdays: [...(r.weekdays ?? [])],
		ends: r.count ? 'count' : r.until ? 'until' : 'never',
		until: r.until ? r.until.slice(0, 10) : '',
		count: r.count ?? 10,
		rrule: friendly ? '' : r.rrule || rrule
	};
}

/** Form state from GET /events/{id}/form `event` values; member names come from GET /events/{id}. */
export function valuesToState(v: EventValues, names: { invited: Ref[]; excluded: Ref[] }, recurrence?: RecurrenceValues | null): FormState {
	const byId = (ids: string[], refs: Ref[]) => ids.map((id) => refs.find((r) => r.id === id) ?? { id, name: '…' });
	return blankState({
		kind: v.kind,
		subtype_id: v.subtype_id ?? '',
		title: v.title,
		start: isoToLocalInput(v.start),
		end: isoToLocalInput(v.end),
		gathering: isoToLocalInput(v.gathering),
		deadline: isoToLocalInput(v.deadline),
		location_id: v.location_id ?? '',
		opponent_id: v.opponent_id ?? '',
		notes: v.notes ?? '',
		is_friendly: v.is_friendly,
		team_ids: [...v.team_ids],
		group_ids: [...v.group_ids],
		club_wide: v.club_wide,
		invited: byId(v.invited_member_ids, names.invited),
		excluded: byId(v.excluded_member_ids, names.excluded),
		...recurrenceToState(recurrence),
		notify: true
	});
}

const orNull = (v: string) => (v ? v : null);

/** What the recurrence part of the form sends: `{rrule}` when the advanced rule is filled in, else `{recurrence}`. */
export function recurrencePayload(s: FormState): { recurrence?: RecurrenceIn; rrule?: string } {
	if (s.rrule.trim()) return { rrule: s.rrule.trim() };
	if (s.repeat === 'none') return {};
	const r: RecurrenceIn = { frequency: s.repeat, interval: Math.max(1, Math.trunc(Number(s.interval) || 1)) };
	if (s.repeat === 'weekly') r.weekdays = WEEKDAY_CODES.filter((c) => s.weekdays.includes(c));
	if (s.ends === 'until' && s.until) r.until = s.until;
	if (s.ends === 'count') r.count = Math.max(1, Math.trunc(Number(s.count) || 1));
	return { recurrence: r };
}
const recurrenceKey = (s: FormState) => JSON.stringify(recurrencePayload(s));
export const recurrenceChanged = (s: FormState, initial: FormState) => recurrenceKey(s) !== recurrenceKey(initial);

/** The audience as sent: a whole-club event lists no teams or groups. */
const audience = (s: FormState) => ({ team_ids: s.club_wide ? [] : s.team_ids, group_ids: s.club_wide ? [] : s.group_ids, club_wide: s.club_wide });

/** POST /events body. */
export function createPayload(s: FormState): EventIn {
	const rec = recurrencePayload(s);
	const repeating = !!(rec.recurrence || rec.rrule);
	const body: EventIn = {
		kind: s.kind,
		subtype_id: orNull(s.subtype_id),
		title: generatesTitle(s.kind) ? '' : s.title.trim(),
		start: normLocal(s.start),
		end: orNull(normLocal(s.end)),
		gathering: orNull(normLocal(s.gathering)),
		deadline: orNull(normLocal(s.deadline)),
		location_id: orNull(s.location_id),
		opponent_id: isGame(s.kind) ? orNull(s.opponent_id) : null,
		notes: s.notes.trim(),
		...audience(s),
		invited_member_ids: s.invited.map((m) => m.id),
		excluded_member_ids: s.excluded.map((m) => m.id),
		notify: s.notify,
		...rec
	};
	// Friendly games are one-off only (the API refuses the flag on a series).
	if (isGame(s.kind) && !repeating) body.is_friendly = s.is_friendly;
	return body;
}

const sameSet = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join();
const TIME_KEYS = ['start', 'end', 'gathering', 'deadline'] as const;

/**
 * PATCH /events/{id} body: only what differs from the loaded values. With a recurrence change the request goes to
 * the series itself (scope all), whose times are those of its first occurrence: then the four times are sent
 * together, moved by `seriesShiftDays` (series first date - this occurrence's date), so only their time of day (and
 * a deliberate date move) reaches the series.
 */
export function patchPayload(s: FormState, initial: FormState, opts: { seriesShiftDays?: number; allowRecurrence?: boolean } = {}): { body: EventPatch; recurrence: boolean } {
	const body: EventPatch = {};
	if (s.kind !== initial.kind) body.kind = s.kind;
	if (s.subtype_id !== initial.subtype_id) body.subtype_id = orNull(s.subtype_id);
	if (generatesTitle(s.kind)) {
		// A generated title follows its inputs: ask the API to make it again when one of them changed.
		const audienceKey = (x: FormState) => `${x.kind}|${x.subtype_id}|${x.club_wide}|${[...x.team_ids].sort()}|${[...x.group_ids].sort()}|${x.opponent_id}|${x.location_id}`;
		if (audienceKey(s) !== audienceKey(initial)) body.title = '';
	} else if (s.title.trim() !== initial.title.trim()) body.title = s.title.trim();
	if (s.location_id !== initial.location_id) body.location_id = orNull(s.location_id);
	const opponent = isGame(s.kind) ? s.opponent_id : '';
	if (opponent !== initial.opponent_id) body.opponent_id = orNull(opponent);
	if (s.notes.trim() !== initial.notes.trim()) body.notes = s.notes.trim();
	const friendly = isGame(s.kind) && s.is_friendly;
	if (friendly !== initial.is_friendly) body.is_friendly = friendly;
	const a = audience(s);
	const ia = audience(initial);
	if (!sameSet(a.team_ids, ia.team_ids)) body.team_ids = a.team_ids;
	if (!sameSet(a.group_ids, ia.group_ids)) body.group_ids = a.group_ids;
	if (a.club_wide !== ia.club_wide) body.club_wide = a.club_wide;
	const inv = s.invited.map((m) => m.id);
	const exc = s.excluded.map((m) => m.id);
	if (!sameSet(inv, initial.invited.map((m) => m.id))) body.invited_member_ids = inv;
	if (!sameSet(exc, initial.excluded.map((m) => m.id))) body.excluded_member_ids = exc;

	const rec = !!opts.allowRecurrence && recurrenceChanged(s, initial);
	const timesChanged = TIME_KEYS.some((k) => normLocal(s[k]) !== normLocal(initial[k]));
	if (rec) {
		Object.assign(body, recurrencePayload(s));
		if (timesChanged) {
			const days = opts.seriesShiftDays ?? 0;
			for (const k of TIME_KEYS) body[k] = orNull(s[k] ? shiftLocal(normLocal(s[k]), days * 1440) : '');
		}
	} else {
		for (const k of TIME_KEYS) if (normLocal(s[k]) !== normLocal(initial[k])) body[k] = orNull(normLocal(s[k]));
	}
	return { body, recurrence: rec };
}

// -- generated game title ---------------------------------------------------------------------------------------------

/**
 * Whether the chosen location makes this an away game: a location that is not one of the club's home venues. No
 * location counts as home (events/services/events.py `_auto_title`).
 */
export function isAwayGame(s: FormState, o: EventOptions): boolean {
	const loc = o.locations.find((l) => l.id === s.location_id);
	return !!loc && !loc.is_home;
}

/** Our teams and groups of the form (a team's short name, else its name; in the order picked, joined with ", "). A whole-club event has none. */
export function audienceNames(s: FormState, o: EventOptions): string {
	if (s.club_wide) return '';
	const teams = [...o.teams, ...(o.filter_teams ?? [])];
	const groups = [...o.groups, ...(o.filter_groups ?? [])];
	return [
		...[...new Set(s.team_ids)].map((id) => teams.find((x) => x.id === id)).filter((x) => !!x).map((x) => x.short_name || x.name),
		...[...new Set(s.group_ids)].map((id) => groups.find((x) => x.id === id)?.name).filter((x) => !!x)
	].join(', ');
}

/** The chosen subtype, when it belongs to the chosen kind. */
export const chosenSubtype = (s: FormState, o: EventOptions) => (o.subtypes ?? []).find((x) => x.id === s.subtype_id && x.kind === s.kind) ?? null;

/** Port of `render_title` in events/services/subtypes.py: fills {teams} and {kind}, tidies the blanks. */
export function renderTemplate(template: string, vars: { teams: string; kind: string }): string {
	return template.replace(/\{(teams|kind)\}/g, (_, k: 'teams' | 'kind') => vars[k]).replace(/\s+/g, ' ').trim();
}

/** Port of `template_for`: the subtype's template, else the kind's default. */
const templateFor = (s: FormState, o: EventOptions) => chosenSubtype(s, o)?.title_template || (s.kind === 'game' ? '{teams}' : s.kind === 'training' ? '{kind} {teams}' : '');

/**
 * The game title the API will generate (port of `_auto_title` in events/services/events.py): the subtype's template
 * (default "{teams}"), {teams} being the match-up with the home side first ("U12 vs Rivals"; at an away location the
 * opponent leads), or just our teams and groups when there is no opponent. With nothing known: `fallback`.
 */
export function gameTitle(s: FormState, o: EventOptions, fallback: string): string {
	const us = audienceNames(s, o);
	const them = o.opponents.find((x) => x.id === s.opponent_id)?.name ?? '';
	const matchUp = us && them ? (isAwayGame(s, o) ? `${them} vs ${us}` : `${us} vs ${them}`) : us || them;
	return renderTemplate(templateFor(s, o), { teams: matchUp, kind: fallback }) || fallback;
}

/**
 * What the title box shows for a title the API generates (a training) or merely suggests (the other kinds): the
 * subtype's template with {teams} and {kind} filled in; empty when there is nothing to suggest.
 */
export function titleSuggestion(s: FormState, o: EventOptions, kindLabel: string): string {
	return renderTemplate(templateFor(s, o), { teams: audienceNames(s, o), kind: kindLabel });
}

// -- labels -----------------------------------------------------------------------------------------------------------

export const localeTag = () => ({ en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' })[getLang()] ?? 'en-GB';

/** Weekday name for a code (MO..SU), from a fixed week (1 Jan 2024 was a Monday). */
export function weekdayName(code: string, style: 'long' | 'short' | 'narrow' = 'long'): string {
	const i = (WEEKDAY_CODES as readonly string[]).indexOf(code);
	if (i < 0) return code;
	return new Intl.DateTimeFormat(localeTag(), { weekday: style, timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1 + i)));
}
/** Weekday code (MO..SU) of the date of a wall-clock value. */
export function weekdayOf(local: string): string | null {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(local);
	if (!m) return null;
	const js = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay(); // 0 = Sunday
	return WEEKDAY_CODES[(js + 6) % 7];
}
/** "31 Dec 2026" for a YYYY-MM-DD date. */
export function formatPlainDate(day: string): string {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
	if (!m) return day;
	return new Intl.DateTimeFormat(localeTag(), { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])));
}
/** "Mon 3 Nov 2026, 18:30" for a wall-clock value. */
export function formatLocal(local: string): string {
	const m = LOCAL_RE.exec(local);
	if (!m) return '';
	return new Intl.DateTimeFormat(localeTag(), { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5])));
}

function joinList(items: string[]): string {
	try {
		return new Intl.ListFormat(localeTag(), { style: 'long', type: 'conjunction' }).format(items);
	} catch {
		return items.join(', ');
	}
}

/** "Every week on Monday and Wednesday until 31 Dec 2026" (empty when not repeating). */
export function describeRecurrence(s: FormState): string {
	if (s.rrule.trim()) return t('eventform.rec.sum.custom', { rule: s.rrule.trim() });
	if (s.repeat === 'none') return '';
	const n = Math.max(1, Math.trunc(Number(s.interval) || 1));
	let text: string;
	if (s.repeat === 'daily') text = n === 1 ? t('eventform.rec.sum.daily_1') : t('eventform.rec.sum.daily_n', { n });
	else if (s.repeat === 'weekly') {
		const days = WEEKDAY_CODES.filter((c) => s.weekdays.includes(c)).map((c) => weekdayName(c));
		if (!days.length) return t('eventform.rec.sum.pick_days');
		text = n === 1 ? t('eventform.rec.sum.weekly_1', { days: joinList(days) }) : t('eventform.rec.sum.weekly_n', { n, days: joinList(days) });
	} else {
		const day = Number(s.start.slice(8, 10)) || 0;
		text = day ? (n === 1 ? t('eventform.rec.sum.monthly_1', { day }) : t('eventform.rec.sum.monthly_n', { n, day })) : n === 1 ? t('eventform.rec.sum.monthly_1_plain') : t('eventform.rec.sum.monthly_n_plain', { n });
	}
	if (s.ends === 'until' && s.until) return t('eventform.rec.sum.until', { text, date: formatPlainDate(s.until) });
	if (s.ends === 'count') return t('eventform.rec.sum.count', { text, n: Math.max(1, Math.trunc(Number(s.count) || 1)) });
	return text;
}

/** The API's field names the form shows next to an input; other field errors go into the summary at the top. */
export const SHOWN_FIELDS = [
	'kind', 'subtype_id', 'title', 'start', 'end', 'gathering', 'deadline', 'location_id', 'opponent_id', 'notes', 'is_friendly', 'team_ids', 'group_ids', 'club_wide',
	'invited_member_ids', 'excluded_member_ids', 'frequency', 'interval', 'weekdays', 'until', 'count', 'rrule', 'recurrence', 'scope'
]; // prettier-ignore

// -- scope ------------------------------------------------------------------------------------------------------------

/** What an edit of an occurrence changes that limits the possible scopes. */
export type ScopeLimits = { recurrence: boolean; dateMoved: boolean; friendly: boolean };
export const SCOPES = ['this', 'following', 'all'] as const;

/** Why the API would refuse `scope` for this edit ('' = possible): a new recurrence needs "all"; a new date or the friendly flag only "this". */
export function scopeBlocked(scope: string, l: ScopeLimits): string {
	if (l.recurrence && scope !== 'all') return t('eventform.scope.need_all');
	if (scope !== 'this' && l.dateMoved) return t('eventform.scope.date_only_this');
	if (scope !== 'this' && l.friendly) return t('eventform.scope.friendly_only_this');
	return '';
}
