/**
 * Evaluations (`/evaluations/*`): the shapes the API returns and the helpers the checklist editor, the fill-in page, the
 * results, the meeting and the member's Evaluations tab share. Two permissions: `can_manage_evaluations` builds checklists,
 * `can_execute_evaluations` gives and reads evaluations (and is the only way to see them).
 */
import type { Ref } from '#lib/events/types.ts';
import { answerText, type FieldOut } from '#lib/forms.ts';

export type ChecklistItem = { id: string; name: string; slug: string; description: string; is_active: boolean; criteria_count: number; evaluation_count: number; created: string };
export type ChecklistDetail = ChecklistItem & { fields: FieldOut[] };
export type ChecklistIn = { name: string; description: string; is_active: boolean };

export type Answer = { field_id: string; key: string; label: string; field_type: FieldOut['field_type']; section: string; order: number; is_active: boolean; options: FieldOut['options']; value: unknown };
export type EvaluationOut = { id: string; checklist: Ref; season: Ref; player: Ref; evaluator: Ref | null; submitted_at: string; answers: Answer[]; public_note?: string; release?: '' | 'notes' | 'full'; released_at?: string | null };

export type StatRow = { field: FieldOut; kind: 'numeric' | 'distribution' | 'text'; response_count: number; average: number | null; minimum: number | null; maximum: number | null; distribution: [string, number][]; recent_answers: string[] };
export type Stats = { season: Ref | null; evaluation_count: number; rows: StatRow[] };
export type Matrix = { season: Ref | null; fields: FieldOut[]; rows: { player: Ref; evaluation_id: string; evaluated_at: string; values: Record<string, unknown> }[] };

export type Entry = { evaluation_id: string; evaluator: Ref | null; at: string; season: Ref; value: unknown };
export type Question = { field: FieldOut; entries: Entry[]; trend: { evaluation_id: string; at: string; value: number }[] };
export type Note = { id: string; note: string; author: Ref | null; created: string };
export type AttendanceBar = { state: 'present' | 'absent' | 'excused' | 'no_reply' | 'upcoming'; kind: string; date: string; title: string };
export type AttendanceCounts = { present: number; absent: number; excused: number; no_reply: number; no_shows: number; total: number; rate: number | null };
export type Attendance = AttendanceCounts & { season: Ref | null; games: AttendanceCounts; practices: AttendanceCounts; recent: AttendanceBar[]; recent_limit: number };
export type Review = { player: Ref; evaluations: EvaluationOut[]; questions: Question[]; notes: Note[]; teams: Ref[]; attendance: Attendance };

export type Session = { id: string; started_at: string; ended_at: string | null; is_open: boolean; started_by: Ref | null };
export type Outcome = { player: Ref; decision: string; recorded_by: Ref | null; modified: string };
export type Meeting = { active: Session | null; latest: Session | null; outcomes: Outcome[] };

export type Season = { id: string; name: string; start_date: string; end_date: string; is_current: boolean };

export const evaluationsHref = '/evaluations';
export const checklistHref = (id: string) => `/evaluations/${id}`;
export const evaluateHref = (id: string, player?: string) => `/evaluations/${id}/evaluate${player ? `?player=${player}` : ''}`;
export const resultsHref = (id: string) => `/evaluations/${id}/results`;
export const meetingHref = (id: string, player?: string) => `/evaluations/${id}/meeting${player ? `?player=${player}` : ''}`;
export const outcomesPdfHref = (sessionId: string) => `/api/v1/evaluations/sessions/${sessionId}/outcomes.pdf`;

/** A stored number (the API keeps them as the string they were typed as) as a number, or null. */
export const num = (v: unknown): number | null => {
	if (v === null || v === undefined || v === '') return null;
	const n = Number(v);
	return Number.isFinite(n) ? n : null;
};
/** Numbers without a trailing ".0" and at most two decimals. */
export const fmt = (n: number) => String(Math.round(n * 100) / 100);

/** An answer as readable text. */
export const answerShown = (a: Pick<Answer, 'field_type' | 'options' | 'value'>, yes: string, no: string) => answerText(a as unknown as FieldOut, a.value, yes, no);

/** Average of an evaluation's numeric answers (criteria on different scales just average out), or null when it has none. */
export function overall(e: EvaluationOut): number | null {
	const values = e.answers.filter((a) => a.field_type === 'number').map((a) => num(a.value)).filter((n): n is number => n !== null);
	return values.length ? values.reduce((s, n) => s + n, 0) / values.length : null;
}

export type Point = { id: string; label: string; value: number };
