/**
 * Attendance statistics as the API returns them (api/schemas_events.py: TeamAttendanceOut, MemberAttendanceOut and
 * the overview's AttendanceCard) plus the small helpers every attendance box shares, so the overview tile, the team
 * page and the member profile format and colour rates and statuses the same way.
 *
 * A rate is present / (present + absent) over past events, as a whole percentage, or null without such answers.
 */
import { getLang, t } from '#lib/i18n.svelte.ts';

export type SeasonRef = { id: string; name: string };
export type Ref = { id: string; name: string };
export type EventRef = { id: string; title: string; kind: string; start: string };

/** GET /overview → `attendance` (null when the season has no past events yet). */
export type ClubAttendance = { rate: number | null; sessions: number };

/** GET /teams/{id}/attendance?season= */
export type PersonRate = { id: string; name: string; rate: number; responses: number };
export type TeamAttendance = {
	season: SeasonRef | null;
	rate: number | null;
	sessions: number;
	top_attenders: PersonRate[];
	needs_attention: PersonRate[];
	missed_recent_practices: Ref[];
	no_shows: { member: Ref; event: EventRef }[];
	no_show_count: number;
};

/** GET /members/{id}/attendance?season= */
export type SparkState = 'present' | 'absent' | 'upcoming';
export type SparkBar = EventRef & { state: SparkState };
export type RecentEvent = { event: EventRef; status: string; showed_up: boolean | null; cancelled: boolean };
export type MemberAttendance = {
	season: SeasonRef | null;
	counts: { present: number; absent: number; no_reply: number; no_shows: number };
	rate: number | null;
	sparkline: SparkBar[];
	recent: RecentEvent[];
};

const LOCALE: Record<string, string> = { en: 'en-GB', nl: 'nl-BE', fr: 'fr-BE' };
const locale = () => LOCALE[getLang()] ?? 'en-GB';

/** 86 → "86%" (or "86 %" in French); null → "—". */
export function formatRate(rate: number | null | undefined): string {
	if (rate === null || rate === undefined) return '—';
	return new Intl.NumberFormat(locale(), { style: 'percent', maximumFractionDigits: 0 }).format(rate / 100);
}

/** Mon 3 Nov 2026 */
export const formatEventDate = (iso: string) => {
	const d = new Date(iso);
	return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat(locale(), { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }).format(d);
};

export type Tone = 'ok' | 'warn' | 'danger' | 'muted';

/** The colour scale for a rate: 80% and up is good, 60–79% worth a look, below 60% a concern. */
export function rateTone(rate: number | null | undefined): Tone {
	if (rate === null || rate === undefined) return 'muted';
	return rate >= 80 ? 'ok' : rate >= 60 ? 'warn' : 'danger';
}

/** Tailwind classes per tone: `text` for a big number, `bar` for a filled bar or swatch, `pill` for a soft badge. */
export const TONE: Record<Tone, { text: string; bar: string; pill: string }> = {
	ok: { text: 'text-ok-text', bar: 'bg-ok', pill: 'bg-ok-bg text-ok-text border-ok-border' },
	warn: { text: 'text-warn-text', bar: 'bg-warn', pill: 'bg-warn-bg text-warn-text border-warn-border' },
	danger: { text: 'text-danger', bar: 'bg-danger', pill: 'bg-danger-bg text-danger border-danger-border' },
	muted: { text: 'text-muted', bar: 'bg-rule', pill: 'bg-subhead text-muted border-line' }
};

/** Tone of an answer: present/selected ok, absent/excused danger, maybe warn, no response / not selected muted (as on the event pages). */
const STATUS_TONE: Record<string, Tone> = { present: 'ok', selected: 'ok', absent: 'danger', excused: 'danger', maybe: 'warn', not_selected: 'muted', no_response: 'muted' };
export const statusTone = (status: string): Tone => STATUS_TONE[status] ?? 'muted';

/** Swatch classes for a sparkline square. */
export const SPARK_STYLE: Record<SparkState, string> = { present: TONE.ok.bar, absent: TONE.danger.bar, upcoming: 'border border-dashed border-line bg-subhead' };

/** Labels; unknown codes fall back to the raw value so they stay visible. */
function labelOr(key: string, raw: string): string {
	const label = t(key);
	return label === key ? raw : label;
}
export const statusLabel = (status: string) => labelOr(`attbox.status.${status}`, status);
export const kindLabel = (kind: string) => labelOr(`attbox.kind.${kind}`, kind);
export const sparkLabel = (state: SparkState) => t(`attbox.spark.${state}`);

export const eventHref = (id: string) => `/calendar/events/${id}`;
export const memberHref = (id: string) => `/members/${id}`;
