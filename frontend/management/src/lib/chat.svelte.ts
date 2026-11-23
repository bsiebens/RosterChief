/**
 * Live state of the chat: one shared list poller (the conversation list and the unread badges read it) and one poller
 * per open conversation. Both run on a self-rescheduling `setTimeout` (never an interval), never run two requests at
 * once, and ignore answers that arrive after `stop()`.
 *
 *   onMount(() => { poller.start(); return () => poller.stop(); })
 */
import {
	chatApi, errorText, firstSeq, lastSeq, mergeMessages, newClientKey, sortConversations, upsertConversation,
	POLL_LIST_HIDDEN, POLL_LIST_VISIBLE, POLL_OPEN_HIDDEN, POLL_OPEN_VISIBLE,
	type Attachment, type Conversation, type Message, type PendingMessage
} from '#lib/chat.ts';
import { t } from '#lib/i18n.svelte.ts';

const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible';

class ListPoller {
	conversations = $state<Conversation[]>([]);
	unreadTotal = $state(0);
	loaded = $state(false);
	error = $state('');
	#users = 0;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#busy = false;
	#listening = false;

	/** Components that need the list call this when they appear and run the returned function when they go; the first starts the timer, the last stops it. */
	acquire(): () => void {
		this.#users++;
		if (this.#users === 1) this.#start();
		let done = false;
		return () => {
			if (done) return;
			done = true;
			this.#users--;
			if (this.#users === 0) this.#stop();
		};
	}

	#start() {
		if (typeof window !== 'undefined' && !this.#listening) {
			window.addEventListener('focus', this.#wake);
			document.addEventListener('visibilitychange', this.#wake);
			this.#listening = true;
		}
		void this.refresh();
	}

	#stop() {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		if (this.#listening) {
			window.removeEventListener('focus', this.#wake);
			document.removeEventListener('visibilitychange', this.#wake);
			this.#listening = false;
		}
	}

	#wake = () => {
		if (this.#users > 0 && visible()) void this.refresh();
	};

	#schedule() {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		if (this.#users === 0) return;
		this.#timer = setTimeout(() => void this.refresh(), visible() ? POLL_LIST_VISIBLE : POLL_LIST_HIDDEN);
	}

	async refresh() {
		if (this.#busy) return;
		this.#busy = true;
		try {
			const res = await chatApi.conversations();
			if (this.#users === 0) return;
			if (res.ok && res.data) {
				this.conversations = sortConversations(res.data.results);
				this.unreadTotal = res.data.unread_total;
				this.error = '';
				this.loaded = true;
			} else if (!this.loaded) {
				this.error = errorText(res, t('chat.err.load'));
			}
		} finally {
			this.#busy = false;
			this.#schedule();
		}
	}

	upsert(c: Conversation) {
		this.conversations = sortConversations(upsertConversation(this.conversations, c));
	}

	/** After a read: the server's total plus a zeroed row, without waiting for the next poll. */
	markRead(id: string, total: number) {
		this.unreadTotal = total;
		this.conversations = this.conversations.map((c) => (c.id === id ? { ...c, unread: 0 } : c));
	}

	remove(id: string) {
		this.conversations = this.conversations.filter((c) => c.id !== id);
	}
}

export const chatList = new ListPoller();

/** One open conversation: its messages, the ones on their way, polling, sending, reactions, reading. */
export class ConversationPoller {
	conversation = $state<Conversation | null>(null);
	messages = $state<Message[]>([]);
	pending = $state<PendingMessage[]>([]);
	hasMoreBefore = $state(false);
	loading = $state(true);
	loadingOlder = $state(false);
	error = $state('');
	readonly id: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#running = false;
	#busy = false;
	#readSeq = 0;
	#readBusy = false;
	/** The server's clock at the last answer: the next poll asks what changed (reactions, deletions) since then, besides the newer messages. */
	#serverTime = '';

	constructor(id: string) {
		this.id = id;
	}

	start() {
		if (this.#running) return;
		this.#running = true;
		document.addEventListener('visibilitychange', this.#wake);
		void this.#load();
	}

	stop() {
		this.#running = false;
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		document.removeEventListener('visibilitychange', this.#wake);
	}

	#wake = () => {
		if (this.#running && visible() && !this.#busy) void this.poll();
	};

	#schedule() {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		if (!this.#running) return;
		this.#timer = setTimeout(() => void this.poll(), visible() ? POLL_OPEN_VISIBLE : POLL_OPEN_HIDDEN);
	}

	async #load() {
		this.#busy = true;
		const res = await chatApi.messages(this.id, { limit: 50 });
		this.#busy = false;
		if (!this.#running) return;
		this.loading = false;
		if (res.ok && res.data) {
			this.messages = mergeMessages([], res.data.messages);
			this.conversation = res.data.conversation;
			this.hasMoreBefore = res.data.has_more_before;
			if (res.data.server_time) this.#serverTime = res.data.server_time;
			this.error = '';
		} else {
			this.error = errorText(res, t('chat.err.load'));
		}
		this.#schedule();
	}

	async poll() {
		if (!this.#running || this.#busy || this.loading || !this.conversation) return;
		this.#busy = true;
		const res = await chatApi.messages(this.id, { after: lastSeq(this.messages), ...(this.#serverTime ? { changed_after: this.#serverTime } : {}) });
		this.#busy = false;
		if (!this.#running) return;
		if (res.ok && res.data) {
			this.messages = mergeMessages(this.messages, [...res.data.messages, ...(res.data.changed ?? [])]);
			this.conversation = res.data.conversation;
			if (res.data.server_time) this.#serverTime = res.data.server_time;
			this.#settle(res.data.messages);
			this.error = '';
		}
		this.#schedule();
	}

	/** Pending messages the server has now returned are done. */
	#settle(incoming: Message[]) {
		const keys = new Set(incoming.map((m) => m.client_key).filter(Boolean));
		if (keys.size) this.pending = this.pending.filter((p) => !keys.has(p.key));
	}

	async loadOlder() {
		if (this.loadingOlder || !this.hasMoreBefore || !this.messages.length) return;
		this.loadingOlder = true;
		const res = await chatApi.messages(this.id, { before: firstSeq(this.messages), limit: 50 });
		this.loadingOlder = false;
		if (!this.#running || !res.ok || !res.data) return;
		this.messages = mergeMessages(this.messages, res.data.messages);
		this.hasMoreBefore = res.data.has_more_before;
	}

	async send(body: string, attachments: Attachment[], urgent: boolean) {
		const item: PendingMessage = { key: newClientKey(), body: body.trim(), attachments, urgent, state: 'sending', error: '', created: new Date().toISOString() };
		this.pending = [...this.pending, item];
		await this.#post(item.key);
	}

	retry(key: string) {
		this.pending = this.pending.map((p) => (p.key === key ? { ...p, state: 'sending', error: '' } : p));
		return this.#post(key);
	}

	discard(key: string) {
		this.pending = this.pending.filter((p) => p.key !== key);
	}

	async #post(key: string) {
		const item = this.pending.find((p) => p.key === key);
		if (!item) return;
		const res = await chatApi.send(this.id, { body: item.body, client_key: key, attachment_ids: item.attachments.map((a) => a.id), urgent: item.urgent || undefined });
		if (!this.#running) return;
		if (res.ok && res.data) {
			this.messages = mergeMessages(this.messages, [res.data]);
			this.pending = this.pending.filter((p) => p.key !== key);
			if (this.conversation) chatList.upsert({ ...this.conversation, last_message: res.data, unread: 0 });
		} else {
			this.pending = this.pending.map((p) => (p.key === key ? { ...p, state: 'failed', error: errorText(res, t('chat.err.send')) } : p));
		}
	}

	upload(file: File) {
		return chatApi.upload(this.id, file);
	}

	/** The highest seq on screen has been seen: tell the server (once per new seq). */
	async markRead(seq: number = lastSeq(this.messages)) {
		if (seq <= this.#readSeq || this.#readBusy || !this.#running) return;
		this.#readBusy = true;
		const res = await chatApi.read(this.id, seq);
		this.#readBusy = false;
		if (res.ok && res.data) {
			this.#readSeq = seq;
			chatList.markRead(this.id, res.data.unread_total);
			if (this.conversation) this.conversation = { ...this.conversation, unread: 0 };
		}
	}

	async react(message: Message, emoji: string) {
		const res = await chatApi.react(message.id, emoji);
		if (res.ok && res.data) this.#patch(message.id, { reactions: res.data.reactions });
		return res;
	}

	async remove(message: Message) {
		const res = await chatApi.deleteMessage(message.id);
		if (res.ok) this.#patch(message.id, { deleted: true, body: '', attachments: [], reactions: [] });
		return res;
	}

	#patch(id: string, change: Partial<Message>) {
		this.messages = this.messages.map((m) => (m.id === id ? { ...m, ...change } : m));
	}

	/** Apply a changed conversation (rename, mute, mode) answered by the API. */
	setConversation(c: Conversation) {
		this.conversation = c;
		chatList.upsert(c);
	}
}
