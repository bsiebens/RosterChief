/**
 * The chat module's contract types (`/api/v1/chat`), the thin API helpers and the pure helpers the components share.
 * Everything here is plain TypeScript (no runes), so it is easy to read and to test.
 */
import { api, type ApiCall, type Fetch } from '#lib/management-api.ts';

export type ChatRole = 'staff' | 'player' | 'parent' | 'admin' | 'other';
export type Person = { member_id: string; name: string; role: ChatRole; role_label: string; photo_url: string | null };
export type Attachment = { id: string; name: string; url: string; content_type: string; size: number };
export type Reaction = { emoji: string; count: number; mine: boolean };
export type Message = {
	id: string;
	seq: number;
	author: Person;
	body: string;
	attachments: Attachment[];
	created: string;
	edited: string | null;
	deleted: boolean;
	mine: boolean;
	urgent: boolean;
	reactions: Reaction[];
	client_key: string | null;
};
export type ConversationKind = 'team' | 'group' | 'direct';
export type ConversationMode = 'discussion' | 'announcement';
export type Conversation = {
	id: string;
	kind: ConversationKind;
	mode: ConversationMode;
	title: string;
	team: { id: string; name: string } | null;
	participant_count: number;
	last_message: Message | null;
	unread: number;
	muted: boolean;
	is_admin: boolean;
	can_post: boolean;
	archived: boolean;
	read_only_parent_view: boolean;
	updated: string;
};
export type Participant = { person: Person; is_admin: boolean; joined_at: string };
export type MessagesPage = { messages: Message[]; conversation: Conversation; has_more_before: boolean };
export type Report = { id: string; message: Message; conversation_id: string; conversation_title: string; reporter: Person; reason: string; created: string; handled: boolean };
export type TeamOption = { id: string; name: string };
export type NewConversation = {
	kind: ConversationKind;
	mode?: ConversationMode;
	title?: string;
	member_ids?: string[];
	team_id?: string;
	include?: { staff: boolean; players: boolean; parents: boolean };
};

/** A message that is on its way (or failed): shown after the server's messages until the server answers. */
export type PendingMessage = { key: string; body: string; attachments: Attachment[]; urgent: boolean; state: 'sending' | 'failed'; error: string; created: string };

export const EMOJI = ['👍', '❤️', '😂', '😮', '🙏', '✅'] as const;
export const MAX_UPLOAD = 10 * 1024 * 1024;
export const POLL_OPEN_VISIBLE = 4000;
export const POLL_OPEN_HIDDEN = 30000;
export const POLL_LIST_VISIBLE = 20000;
export const POLL_LIST_HIDDEN = 60000;

const B = '/chat';
export const chatApi = {
	conversations: (f?: Fetch) => api<{ results: Conversation[]; unread_total: number }>('GET', `${B}/conversations`, { f }),
	create: (body: NewConversation) => api<Conversation>('POST', `${B}/conversations`, { body }),
	detail: (id: string) => api<{ conversation: Conversation; participants: Participant[] }>('GET', `${B}/conversations/${id}`),
	messages: (id: string, query: { after?: number; before?: number; limit?: number } = {}) => api<MessagesPage>('GET', `${B}/conversations/${id}/messages`, { query }),
	send: (id: string, body: { body: string; client_key: string; attachment_ids?: string[]; urgent?: boolean }) => api<Message>('POST', `${B}/conversations/${id}/messages`, { body }),
	upload: (id: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return api<Attachment>('POST', `${B}/conversations/${id}/attachments`, { form });
	},
	read: (id: string, seq: number) => api<{ unread_total: number }>('POST', `${B}/conversations/${id}/read`, { body: { seq } }),
	patch: (id: string, body: { title?: string; mode?: ConversationMode; muted?: boolean; archived?: boolean }) => api<Conversation>('PATCH', `${B}/conversations/${id}`, { body }),
	addParticipants: (id: string, member_ids: string[]) => api<Participant[]>('POST', `${B}/conversations/${id}/participants`, { body: { member_ids } }),
	removeParticipant: (id: string, memberId: string) => api('DELETE', `${B}/conversations/${id}/participants/${memberId}`),
	deleteMessage: (id: string) => api('DELETE', `${B}/messages/${id}`),
	react: (id: string, emoji: string) => api<{ reactions: Reaction[] }>('POST', `${B}/messages/${id}/reactions`, { body: { emoji } }),
	report: (id: string, reason: string) => api('POST', `${B}/messages/${id}/report`, { body: { reason } }),
	people: (q: string) => api<{ results: Person[] }>('GET', `${B}/people`, { query: { q, limit: 20 } }),
	teams: () => api<{ results: TeamOption[] }>('GET', `${B}/teams`),
	reports: () => api<{ results: Report[] }>('GET', `${B}/moderation/reports`),
	handleReport: (id: string) => api('POST', `${B}/moderation/reports/${id}/handle`),
	moderationMessages: (id: string) => api<MessagesPage>('GET', `${B}/moderation/conversations/${id}/messages`)
};

export const errorText = (res: ApiCall<unknown>, fallback: string) => (res.code === 'network' ? fallback : res.message || fallback);

/** Does this person see the chat at all (feature on and allowed)? */
export function chatEnabled(me: { features: string[]; can_use_chat?: boolean } | null | undefined): boolean {
	return !!me && me.features.includes('chat') && me.can_use_chat === true;
}

// ---- messages ------------------------------------------------------------------------------------------------

/** Merge incoming messages into the list: same id replaces, result ascending by seq. */
export function mergeMessages(existing: Message[], incoming: Message[]): Message[] {
	if (!incoming.length) return existing;
	const byId = new Map(existing.map((m) => [m.id, m]));
	for (const m of incoming) byId.set(m.id, m);
	return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

export const lastSeq = (messages: Message[]) => messages.reduce((n, m) => Math.max(n, m.seq), 0);
export const firstSeq = (messages: Message[]) => messages.reduce((n, m) => Math.min(n, m.seq), Infinity);

export const newClientKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`);

/** Newest activity first. */
export function sortConversations(list: Conversation[]): Conversation[] {
	const stamp = (c: Conversation) => c.last_message?.created ?? c.updated;
	return [...list].sort((a, b) => (stamp(a) < stamp(b) ? 1 : stamp(a) > stamp(b) ? -1 : 0));
}

/** Replace the conversation of the same id (or add it). */
export const upsertConversation = (list: Conversation[], c: Conversation): Conversation[] => (list.some((x) => x.id === c.id) ? list.map((x) => (x.id === c.id ? c : x)) : [c, ...list]);

/** Who sees a role label next to the name: staff and admins only. */
export const showRoleLabel = (p: Person) => (p.role === 'staff' || p.role === 'admin') && !!p.role_label;

export function bubbleClass(role: ChatRole): string {
	if (role === 'staff') return 'bg-club text-club-content';
	if (role === 'admin') return 'bg-ink text-white';
	if (role === 'parent') return 'bg-sky-100 text-ink';
	return 'bg-slate-200 text-ink';
}

export const isImage = (a: Attachment) => a.content_type.startsWith('image/');

export function fileSize(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Why a file cannot be attached ('' when it can). Returns an i18n key. */
export function attachProblem(file: { type: string; size: number; name: string }): '' | 'chat.err.file_type' | 'chat.err.file_size' {
	const pdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
	if (!file.type.startsWith('image/') && !pdf) return 'chat.err.file_type';
	return file.size > MAX_UPLOAD ? 'chat.err.file_size' : '';
}

/** Can the composer send: some text or an attachment, and no upload still running. */
export const canSend = (body: string, attachments: number, uploading: boolean) => !uploading && (body.trim().length > 0 || attachments > 0);

export const isNearBottom = (el: { scrollHeight: number; scrollTop: number; clientHeight: number }, slack = 80) => el.scrollHeight - el.scrollTop - el.clientHeight <= slack;

// ---- dates ---------------------------------------------------------------------------------------------------

const dayStamp = (d: Date) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
export const dayKey = (iso: string) => dayStamp(new Date(iso));

/** 'today' / 'yesterday' / a formatted date, for the day separators. */
export function dayLabel(iso: string, lang: string, now: Date = new Date()): { kind: 'today' | 'yesterday' | 'date'; text: string } {
	const d = new Date(iso);
	if (dayStamp(d) === dayStamp(now)) return { kind: 'today', text: '' };
	const y = new Date(now);
	y.setDate(y.getDate() - 1);
	if (dayStamp(d) === dayStamp(y)) return { kind: 'yesterday', text: '' };
	return { kind: 'date', text: d.toLocaleDateString(lang, { weekday: 'long', day: 'numeric', month: 'long', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) }) };
}

export const timeLabel = (iso: string, lang: string) => new Date(iso).toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' });

/** Short stamp for the list: the time today, else the day and month. */
export function listStamp(iso: string, lang: string, now: Date = new Date()): string {
	const d = new Date(iso);
	return dayStamp(d) === dayStamp(now) ? timeLabel(iso, lang) : d.toLocaleDateString(lang, { day: 'numeric', month: 'short' });
}

export type ListEntry = { type: 'day'; key: string; iso: string } | { type: 'message'; key: string; message: Message; pending: false } | { type: 'pending'; key: string; message: PendingMessage };

/** Messages (and the pending ones) with a day entry before the first message of each day. */
export function withDaySeparators(messages: Message[], pending: PendingMessage[]): ListEntry[] {
	const out: ListEntry[] = [];
	let day = '';
	const add = (iso: string) => {
		const k = dayKey(iso);
		if (k !== day) {
			day = k;
			out.push({ type: 'day', key: `day-${k}`, iso });
		}
	};
	for (const m of messages) {
		add(m.created);
		out.push({ type: 'message', key: m.id, message: m, pending: false });
	}
	for (const p of pending) {
		add(p.created);
		out.push({ type: 'pending', key: `p-${p.key}`, message: p });
	}
	return out;
}

/** What the area under the messages shows. */
export function composerMode(c: Pick<Conversation, 'can_post' | 'read_only_parent_view' | 'archived'>): 'compose' | 'parent_view' | 'archived' | 'announce_only' {
	if (c.read_only_parent_view) return 'parent_view';
	if (c.archived) return 'archived';
	if (!c.can_post) return 'announce_only';
	return 'compose';
}

/** Group the participants by role for the panel, in a fixed order. */
export function groupParticipants(list: Participant[]): { role: ChatRole; people: Participant[] }[] {
	const order: ChatRole[] = ['admin', 'staff', 'player', 'parent', 'other'];
	return order.map((role) => ({ role, people: list.filter((p) => p.person.role === role).sort((a, b) => a.person.name.localeCompare(b.person.name)) })).filter((g) => g.people.length);
}

export function splitName(name: string): { first: string; last: string } {
	const parts = name.trim().split(/\s+/);
	return { first: parts[0] ?? '', last: parts.length > 1 ? parts[parts.length - 1] : '' };
}
