<script lang="ts">
	import { ArrowDown, LoaderCircle } from '@lucide/svelte';
	import { tick, untrack } from 'svelte';
	import {
		dayLabel, isNearBottom, lastSeq, myPerson, pendingToMessage, withDaySeparators,
		type Message, type PendingMessage
	} from '#lib/chat.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import ChatBubble from './ChatBubble.svelte';

	/**
	 * The scrolling list of messages with day separators. It stays glued to the bottom while I am there, shows a floating "new messages" pill
	 * when something arrives while I have scrolled up, keeps its place when earlier messages are prepended, and calls `onseen(seq)` when the
	 * newest message is on screen (the caller marks the conversation read).
	 */
	let {
		messages, pending = [], hasMoreBefore = false, loadingOlder = false, announcement = false, canDelete = false, readonly = false, highlightId = '',
		onloadolder, onseen, onreact, ondelete, onreport, onreplyprivately, onretry, ondiscard
	}: {
		messages: Message[];
		pending?: PendingMessage[];
		hasMoreBefore?: boolean;
		loadingOlder?: boolean;
		announcement?: boolean;
		canDelete?: boolean;
		/** No reactions or sending actions (the moderation view). */
		readonly?: boolean;
		highlightId?: string;
		onloadolder?: () => Promise<void> | void;
		onseen?: (seq: number) => void;
		onreact?: (message: Message, emoji: string) => void;
		ondelete?: (message: Message) => void;
		onreport?: (message: Message) => void;
		onreplyprivately?: (message: Message) => void;
		onretry?: (key: string) => void;
		ondiscard?: (key: string) => void;
	} = $props();

	let scroller: HTMLElement | undefined = $state();
	let atBottom = $state(true);
	let seenSeq = $state(0);
	let first = true;

	const entries = $derived(withDaySeparators(messages, pending));
	const me = $derived(myPerson(messages));
	const newest = $derived(lastSeq(messages));
	const unseen = $derived(messages.filter((m) => m.seq > seenSeq && !m.mine && !m.deleted).length);
	const lang = $derived(getLang());

	function toBottom(smooth = false) {
		if (!scroller) return;
		scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
	}

	function onscroll() {
		if (scroller) atBottom = isNearBottom(scroller);
	}

	// New content: stay at the bottom when I am there (or when I just sent something), otherwise leave the scroll position alone.
	$effect(() => {
		const count = messages.length + pending.length;
		const mineLast = pending.length > 0 || messages[messages.length - 1]?.mine === true;
		void count;
		untrack(async () => {
			if (!scroller) return;
			const stick = first || atBottom || mineLast;
			first = false;
			if (stick) {
				await tick();
				toBottom();
				atBottom = true;
			}
		});
	});

	// Seen: whatever is at the bottom of a visible page is read.
	$effect(() => {
		if (atBottom && newest > 0) {
			seenSeq = newest;
			if (typeof document === 'undefined' || document.visibilityState === 'visible') onseen?.(newest);
		}
	});

	// Coming back to the tab while at the bottom counts as seeing the newest message.
	function visibilityBack() {
		if (document.visibilityState === 'visible' && atBottom && newest > 0) onseen?.(newest);
	}
	$effect(() => {
		document.addEventListener('visibilitychange', visibilityBack);
		return () => document.removeEventListener('visibilitychange', visibilityBack);
	});

	async function older() {
		if (!scroller || !onloadolder) return;
		const before = scroller.scrollHeight - scroller.scrollTop;
		await onloadolder();
		await tick();
		if (scroller) scroller.scrollTop = scroller.scrollHeight - before;
	}

	const dayText = (iso: string) => {
		const d = dayLabel(iso, lang);
		return d.kind === 'today' ? t('chat.day.today') : d.kind === 'yesterday' ? t('chat.day.yesterday') : d.text;
	};
</script>

<div class="relative min-h-0 flex-1">
	<div bind:this={scroller} {onscroll} class="flex h-full flex-col gap-1.5 overflow-x-hidden overflow-y-auto bg-paper/50 px-3 py-3 sm:px-4" role="log" aria-live="polite" aria-relevant="additions" aria-label={t('chat.messages.label')}>
		{#if hasMoreBefore}
			<div class="flex justify-center py-1">
				<button type="button" class="btn btn-outline btn-sm" disabled={loadingOlder} onclick={older}>
					{#if loadingOlder}<LoaderCircle class="animate-spin" size={14} aria-hidden="true" />{/if}{t('chat.messages.older')}
				</button>
			</div>
		{/if}
		{#each entries as entry, i (entry.key)}
			{#if entry.type === 'day'}
				<div class="my-2 flex items-center gap-3 text-[11px] font-semibold tracking-wide text-muted uppercase" role="separator">
					<span class="h-px flex-1 bg-line"></span><span>{dayText(entry.iso)}</span><span class="h-px flex-1 bg-line"></span>
				</div>
			{:else}
				{@const previous = entries[i - 1]}
				{@const message = entry.type === 'pending' ? pendingToMessage(entry.message, me) : entry.message}
				{@const grouped = !message.mine && previous?.type === 'message' && previous.message.author.member_id === message.author.member_id}
				<div class="rounded-xl {highlightId && message.id === highlightId ? 'bg-warn-bg ring-2 ring-warn' : ''}">
					{#if entry.type === 'pending'}
						<ChatBubble {message} status={entry.message.state} error={entry.message.error} showName={false} onretry={() => onretry?.(entry.message.key)} ondiscard={() => ondiscard?.(entry.message.key)} myRole={me.role} />
					{:else}
						<ChatBubble {message} showName={!grouped} {announcement} {canDelete} myRole={me.role} onreact={readonly ? undefined : onreact} {ondelete} {onreport} {onreplyprivately} />
					{/if}
				</div>
			{/if}
		{:else}
			<p class="m-auto py-10 text-center text-sm text-muted">{t('chat.messages.empty')}</p>
		{/each}
	</div>
	{#if !atBottom && unseen > 0}
		<button type="button" class="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-club px-3.5 py-1.5 text-xs font-bold text-club-content shadow-lg" onclick={() => toBottom(true)}>
			<ArrowDown size={14} aria-hidden="true" />{unseen === 1 ? t('chat.messages.new') : t('chat.messages.new_n', { n: unseen })}
		</button>
	{/if}
</div>
