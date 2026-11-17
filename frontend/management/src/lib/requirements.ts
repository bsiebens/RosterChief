/**
 * Requirements (`/requirements/*`): what a club asks of its members -- documents (a medical certificate, a photo ...) or questions (extra
 * information) -- and each member's checklist per season. Managing needs `members.manage_members`; reading a member's checklist needs the
 * right to see them.
 */
export const EVENT_KINDS = ['training', 'game', 'tournament', 'meeting', 'social', 'other'] as const;

export type RequirementKind = 'document' | 'question';
export type QuestionType = 'text' | 'textarea' | 'number' | 'date' | 'choice' | 'multichoice' | 'yes_no';
export const QUESTION_TYPES: QuestionType[] = ['text', 'textarea', 'number', 'date', 'choice', 'multichoice', 'yes_no'];
export const hasChoices = (type: string) => type === 'choice' || type === 'multichoice';
/** A choice is a plain string or a `{value, label}` object (what is stored is the value). */
export type ChoiceOption = string | { value: string; label: string };

/** What a question needs to be rendered and its answer shown: the same on the staff checklist and the registrant's status page. */
export type QuestionShape = { question_type: string; options: ChoiceOption[] | null; min_value?: string | number | null; max_value?: string | number | null };

export type Requirement = QuestionShape & {
	id: string;
	name: string;
	description: string;
	kind: RequirementKind;
	requires_document: boolean;
	/** Staff must send the member a file before the member can hand anything in. */
	needs_staff_file: boolean;
	/** What the person completing it has to do, shown to the member and guardians. */
	member_instructions: string;
	blocked_event_kinds: string[];
	is_active: boolean;
	/** Months a completion stays valid; null = never expires. */
	valid_for_months: number | null;
	/** Days before expiry that the member (or their guardians) is reminded. */
	reminder_days_before: number;
};
export type RequirementListItem = Requirement & { usage_count: number };

export type DocDisplay = 'missing' | 'pending' | 'declined' | 'requested' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed';
export type HistoryKind = 'submitted' | 'approved' | 'declined' | 'requested' | 'reopened' | 'bypassed' | 'sent_file' | 'completed';
/** One step in a requirement's history (staff see everything; the member only decisions and messages from the club). */
export type RequirementEvent = { id: string; kind: HistoryKind; actor_name: string; note: string; created: string };

/** Needs the member to act (hand something in, again): used for the tone of the member's own screen and the counts. */
export const needsMemberAction = (display: string) => display === 'missing' || display === 'expired' || display === 'declined' || display === 'requested';
export type ChecklistItem = {
	requirement: Requirement;
	/** An expired completion is `open` again. */
	state: 'open' | 'complete' | 'bypassed';
	display: DocDisplay;
	note: string;
	completed_at: string | null;
	completed_by: string | null;
	has_document: boolean;
	/** A question's answer: text, a number as a string, an ISO date, a list of option values or a boolean. Null while open. */
	answer: unknown;
	valid_until: string | null;
	days_left: number | null;
	/** The latest reason (declined) or message (requested) the member sees. */
	review_note: string;
	/** A personalised file the club sent this member, with the note that came with it. */
	has_sent_file: boolean;
	/** Open, and the club still has to send its document before the member can hand anything in. */
	awaiting_file: boolean;
	sent_at: string | null;
	sent_note: string;
	submitted_at: string | null;
	history: RequirementEvent[];
};
export type Checklist = {
	membership: { id: string; season: { id: string; name: string }; status: string; fee_status: string } | null;
	items: ChecklistItem[];
	total: number;
	done: number;
	open: number;
	expiring: number;
	expired: number;
};

export const documentHref = (membershipId: string, requirementId: string) => `/api/v1/requirements/memberships/${membershipId}/${requirementId}/document`;

export const sentFileHref = (membershipId: string, requirementId: string) => `/api/v1/requirements/memberships/${membershipId}/${requirementId}/sent-file`;

export const choicesOf = (q: Pick<QuestionShape, 'options'>): { value: string; label: string }[] => (q.options ?? []).map((o) => (typeof o === 'string' ? { value: o, label: o } : o));

/** The options as the editor's lines: `label`, or `value | label` when they differ. */
export const optionLines = (q: Pick<QuestionShape, 'options'>): string => choicesOf(q).map((o) => (o.value === o.label ? o.label : `${o.value} | ${o.label}`)).join('\n');
/** The editor's lines as options for the API (a plain string, or `{value, label}` for `value | label`). Blank lines are dropped. */
export function parseOptionLines(text: string): ChoiceOption[] {
	return text
		.split('\n')
		.map((l) => l.trim())
		.filter(Boolean)
		.map((l) => {
			const [value, ...rest] = l.split('|');
			const label = rest.join('|').trim();
			return label ? { value: value.trim(), label } : l;
		});
}

/** An answer as readable text. `formatDate` shows a date the way the page does; `yes` / `no` are the translated words. */
export function answerText(q: QuestionShape, answer: unknown, yes: string, no: string, formatDate?: (iso: string) => string): string {
	if (answer === null || answer === undefined || answer === '') return '';
	if (q.question_type === 'yes_no') return answer === true || answer === 'true' ? yes : no;
	if (q.question_type === 'date') return formatDate ? formatDate(String(answer)) : String(answer);
	const label = (v: unknown) => choicesOf(q).find((o) => o.value === String(v))?.label ?? String(v);
	if (q.question_type === 'multichoice') return (Array.isArray(answer) ? answer : [answer]).map(label).join(', ');
	if (q.question_type === 'choice') return label(answer);
	return String(answer);
}

/** The answer from submitted form data (`answer` entries): a list for a multiple choice, else the single value (empty string when nothing was given). */
export function answerFromForm(type: string, entries: string[]): string | string[] {
	return type === 'multichoice' ? entries.filter((x) => x !== '') : (entries[0] ?? '');
}
