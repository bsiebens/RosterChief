/**
 * Forms (`/forms/*`): the shapes the API returns and the helpers the form editor, the send page and the responses view
 * share. Everything needs `forms.manage_forms`. List endpoints are expected as `{count, results}`; if the backend ever
 * answers with a bare array, `asList` below is the one place that adapts it.
 */
import type { Ref } from '#lib/events/types.ts';

export type FieldType = 'text' | 'textarea' | 'number' | 'email' | 'date' | 'choice' | 'multichoice' | 'checkbox' | 'file';
export const FIELD_TYPES: FieldType[] = ['text', 'textarea', 'number', 'email', 'date', 'choice', 'multichoice', 'checkbox', 'file'];
export const hasOptions = (type: FieldType) => type === 'choice' || type === 'multichoice';

export type OptionOut = string | { value: string; label: string };
export type FieldOut = {
	id: string;
	key: string;
	label: string;
	field_type: FieldType;
	required: boolean;
	help_text: string;
	section: string;
	order: number;
	is_active: boolean;
	options: OptionOut[] | null;
	/** Number fields: the lowest / highest answer allowed (the API sends decimals as strings). */
	min_value?: string | number | null;
	max_value?: string | number | null;
};
/** What PUT /forms/{id}/fields takes: a field as returned, with `id` / `key` left out for new ones. */
export type FieldIn = Omit<FieldOut, 'id' | 'key' | 'order' | 'min_value' | 'max_value'> & { id?: string; key?: string; min_value?: number | null; max_value?: number | null };

export type FormListItem = {
	id: string;
	title: string;
	slug: string;
	description: string;
	is_active: boolean;
	login_required: boolean;
	field_count: number;
	send_count: number;
	response_count: number;
	created: string;
};
export type FormDetail = FormListItem & { fields: FieldOut[] };
export type FormIn = { title: string; description: string; login_required: boolean; is_active: boolean };

export type SendStatus = 'scheduled' | 'open' | 'closed' | 'inactive';
export type SendListItem = {
	id: string;
	form_id: string;
	form_title: string;
	audience_label: string;
	opens_at: string | null;
	closes_at: string | null;
	is_active: boolean;
	is_public: boolean;
	status: SendStatus;
	audience_count: number;
	response_count: number;
	created: string;
	created_by: Ref | null;
};
export type SendDetail = SendListItem & {
	teams: Ref[];
	groups: Ref[];
	invited_members: Ref[];
	excluded_members: Ref[];
	club_wide: boolean;
	season: Ref | null;
	max_submissions_per_user: number | null;
	public_token: string | null;
	public_url_path: string | null;
	reminder_sent_at: string | null;
};
export type SendIn = {
	team_ids: string[];
	group_ids: string[];
	club_wide: boolean;
	invited_member_ids: string[];
	excluded_member_ids: string[];
	season_id?: string | null;
	opens_at: string | null;
	closes_at: string | null;
	max_submissions_per_user: number | null;
	is_public: boolean;
	notify: boolean;
};
/** PATCH /form-sends/{id}: the open / close window, limit, public link and active flag. */
export type SendPatch = Partial<Pick<SendIn, 'opens_at' | 'closes_at' | 'max_submissions_per_user' | 'is_public'> & { is_active: boolean }>;

export type ReportRow = {
	submission_id: string;
	member: Ref | null;
	submitted_by: Ref | null;
	on_behalf: boolean;
	submitted_at: string;
	values: Record<string, unknown>;
	files: Record<string, { answer_id: string; name: string }>;
};
export type Report = {
	send: SendListItem;
	columns: FieldOut[];
	rows: ReportRow[];
	summaries: Record<string, Record<string, number>>;
	count: number;
	not_responded: Ref[];
};

export type PagedList<T> = { count: number; results: T[] };
/** The list endpoints answer `{count, results}`; tolerate a bare array too. */
export function asList<T>(data: PagedList<T> | T[] | undefined): PagedList<T> {
	if (!data) return { count: 0, results: [] };
	return Array.isArray(data) ? { count: data.length, results: data } : data;
}

export const formHref = (id: string) => `/forms/${id}`;
export const sendFormHref = (id: string) => `/forms/${id}/send`;
export const sendHref = (id: string) => `/forms/sends/${id}`;
export const reportCsvHref = (sendId: string) => `/api/v1/form-sends/${sendId}/report.csv`;
export const answerFileHref = (answerId: string) => `/api/v1/form-answers/${answerId}/file`;
/** The public fill-in URL on this club's own host. */
export const publicUrl = (path: string, origin: string) => `${origin}${path}`;

// -- the field builder's working copy ---------------------------------------------------------------------------------

export type OptionDraft = { uid: string; value: string; label: string; stored: boolean };
export type FieldDraft = {
	/** Local identity for keyed lists and element ids; not sent. */
	uid: string;
	id?: string;
	key?: string;
	label: string;
	field_type: FieldType;
	required: boolean;
	help_text: string;
	section: string;
	is_active: boolean;
	options: OptionDraft[];
	/** Number fields: what was typed in the range boxes ('' = no limit). */
	min: string;
	max: string;
};

let counter = 0;
const uid = () => `d${++counter}`;

export const optionOf = (o: OptionOut): OptionDraft => (typeof o === 'string' ? { uid: uid(), value: o, label: o, stored: true } : { uid: uid(), value: o.value, label: o.label, stored: true });
export const blankOption = (): OptionDraft => ({ uid: uid(), value: '', label: '', stored: false });
export const blankField = (): FieldDraft => ({ uid: uid(), label: '', field_type: 'text', required: false, help_text: '', section: '', is_active: true, options: [], min: '', max: '' });
/** A stored limit as it is typed: no limit is empty, "1.0000" is 1. */
const limitText = (v: string | number | null | undefined) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '' : String(Number(v)));
const limitOf = (text: string) => (text.trim() !== '' && Number.isFinite(Number(text.replace(',', '.'))) ? Number(text.replace(',', '.')) : null);

export const toDraft = (f: FieldOut): FieldDraft => ({
	uid: uid(),
	id: f.id,
	key: f.key,
	label: f.label,
	field_type: f.field_type,
	required: f.required,
	help_text: f.help_text,
	section: f.section,
	is_active: f.is_active,
	options: (f.options ?? []).map(optionOf),
	min: limitText(f.min_value),
	max: limitText(f.max_value)
});
export const draftsOf = (f: { fields: FieldOut[] }): FieldDraft[] => [...f.fields].sort((a, b) => a.order - b.order).map(toDraft);

/** The value stored in answers: kept for options that were saved before, otherwise it follows what was typed. */
const valueOf = (o: OptionDraft) => (o.stored ? o.value : o.label.trim());

/** A draft as PUT /forms/{id}/fields takes it. Options without a label are dropped; label == value goes as a plain string. */
export function draftToIn(d: FieldDraft): FieldIn {
	const options = hasOptions(d.field_type)
		? d.options
				.filter((o) => o.label.trim())
				.map((o): OptionOut => (valueOf(o) === o.label.trim() ? o.label.trim() : { value: valueOf(o), label: o.label.trim() }))
		: null;
	return {
		...(d.id ? { id: d.id } : {}),
		...(d.key ? { key: d.key } : {}),
		label: d.label.trim(),
		field_type: d.field_type,
		required: d.required,
		help_text: d.help_text.trim(),
		section: d.section.trim(),
		is_active: d.is_active,
		options,
		min_value: d.field_type === 'number' ? limitOf(d.min) : null,
		max_value: d.field_type === 'number' ? limitOf(d.max) : null
	};
}
/** Comparable snapshot of the list, for the unsaved-changes check. */
export const draftsKey = (list: FieldDraft[]) => JSON.stringify(list.map(draftToIn));

/** The option label to show for a stored value. */
export function optionLabel(f: Pick<FieldOut, 'options'>, value: string): string {
	for (const o of f.options ?? []) if (typeof o === 'string' ? o === value : o.value === value) return typeof o === 'string' ? o : o.label;
	return value;
}
export const optionsOf = (f: Pick<FieldOut, 'options'>): { value: string; label: string }[] => (f.options ?? []).map((o) => (typeof o === 'string' ? { value: o, label: o } : o));

/** An answer value as readable text for the responses table (files render as links elsewhere). */
export function answerText(f: FieldOut, v: unknown, yes: string, no: string): string {
	if (v === null || v === undefined || v === '') return '';
	if (f.field_type === 'checkbox') return v === true || v === 'true' || v === 'on' ? yes : no;
	if (f.field_type === 'multichoice') return (Array.isArray(v) ? v : [v]).map((x) => optionLabel(f, String(x))).join(', ');
	if (f.field_type === 'choice') return optionLabel(f, String(v));
	return String(v);
}

/** Group consecutive fields by `section` for the preview (a field with an empty section continues the previous one). */
export function bySection<T extends { section: string }>(list: T[]): { section: string; items: T[] }[] {
	const out: { section: string; items: T[] }[] = [];
	for (const f of list) {
		const last = out[out.length - 1];
		if (last && (!f.section || f.section === last.section)) last.items.push(f);
		else out.push({ section: f.section, items: [f] });
	}
	return out;
}
