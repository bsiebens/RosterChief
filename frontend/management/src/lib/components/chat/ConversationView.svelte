<script lang="ts">
	import { Eye, LoaderCircle, MessageCircleReply } from '@lucide/svelte';
	import { onMount, untrack } from 'svelte';
	import { chatApi, composerMode, errorText, type Conversation, type Message } from '#lib/chat.ts';
	import { ConversationPoller } from '#lib/chat.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import Composer from './Composer.svelte';
	import ConversationHeader from './ConversationHeader.svelte';
	import MessageList from './MessageList.svelte';
	import ParticipantsPanel from './ParticipantsPanel.svelte';

	/**
	 * One open conversation: header, messages, composer (or the note that replaces it) and the participants (inline from the `xl` breakpoint,
	 * otherwise in a dialog). It owns the poller, which starts here and stops when the view goes away; give it a `{#key id}`.
	 */
	let { id, onback, onopen, peopleDefault = false }: {
		id: string;
		onback?: () => void;
		/** Open another conversation (after "Reply privately"). */
		onopen: (c: Conversation) => void;
		/** Show the participants next to the messages when there is room. */
		peopleDefault?: boolean;
	} = $props();

	const poller = new ConversationPoller(untrack(() => id));
	const c = $derived(poller.conversation);
	const mode = $derived(c ? composerMode(c) : 'compose');

	let wide = $state(false);
	let showPeople = $state(false);
	let reporting = $state<Message | null>(null);
	let reason = $state('');
	let reportBusy = $state(false);
	let reportError = $state('');
	let reportDone = $state(false);
	let notice = $state('');
	let replyBusy = $state(false);

	onMount(() => {
		const query = matchMedia('(min-width: 1280px)');
		const sync = () => {
			wide = query.matches;
		};
		sync();
		showPeople = peopleDefault && wide;
		query.addEventListener('change', sync);
		poller.start();
		return () => {
			query.removeEventListener('change', sync);
			poller.stop();
		};
	});

	// The person a private reply goes to when the composer is replaced by the note: the author of the latest message that is not mine.
	const replyTarget = $derived([...poller.messages].reverse().find((m) => !m.mine && !m.deleted)?.author ?? null);

	async function replyPrivately(person: { member_id: string } | null) {
		if (!person || replyBusy) return;
		replyBusy = true;
		notice = '';
		const res = await chatApi.create({ kind: 'direct', member_ids: [person.member_id] });
		replyBusy = false;
		if (res.ok && res.data) onopen(res.data);
		else notice = errorText(res, t('chat.err.generic'));
	}

	async function remove(message: Message) {
		if (!confirm(t('chat.msg.delete_confirm'))) return;
		notice = '';
		const res = await poller.remove(message);
		if (!res.ok) notice = errorText(res, t('chat.err.generic'));
	}

	async function react(message: Message, emoji: string) {
		const res = await poller.react(message, emoji);
		if (!res.ok) notice = errorText(res, t('chat.err.generic'));
	}

	function startReport(message: Message) {
		reporting = message;
		reason = '';
		reportError = '';
		reportDone = false;
	}

	async function sendReport(e: SubmitEvent) {
		e.preventDefault();
		if (!reporting || reportBusy) return;
		reportBusy = true;
		reportError = '';
		const res = await chatApi.report(reporting.id, reason.trim());
		reportBusy = false;
		if (res.ok) reportDone = true;
		else reportError = errorText(res, t('chat.err.generic'));
	}
</script>

<div class="flex h-full min-h-0">
	<section class="flex min-w-0 flex-1 flex-col bg-white" aria-label={c?.title ?? t('chat.title')}>
		{#if poller.loading}
			{#if onback}<div class="border-b border-line p-2 md:hidden"><button type="button" class="btn btn-ghost btn-sm" onclick={onback}>{t('chat.back')}</button></div>{/if}
			<div class="m-auto flex items-center gap-2 text-sm text-muted" role="status"><LoaderCircle class="animate-spin" size={16} aria-hidden="true" />{t('chat.loading')}</div>
		{:else if !c}
			<div class="m-auto grid justify-items-center gap-3 p-6 text-center" role="alert">
				<p class="text-sm text-danger">{poller.error || t('chat.err.load')}</p>
				<div class="flex gap-2">
					{#if onback}<button type="button" class="btn btn-outline btn-sm md:hidden" onclick={onback}>{t('chat.back')}</button>{/if}
					<button type="button" class="btn btn-primary btn-sm" onclick={() => location.reload()}>{t('chat.retry')}</button>
				</div>
			</div>
		{:else}
			<ConversationHeader conversation={c} {onback} onpeople={() => (showPeople = !showPeople)} peopleOpen={showPeople} />
			{#if poller.error}<p class="bg-danger-bg px-3 py-1.5 text-xs text-danger" role="alert">{poller.error}</p>{/if}
			{#if notice}<p class="bg-danger-bg px-3 py-1.5 text-xs text-danger" role="alert">{notice}</p>{/if}
			<MessageList
				messages={poller.messages}
				pending={poller.pending}
				hasMoreBefore={poller.hasMoreBefore}
				loadingOlder={poller.loadingOlder}
				announcement={c.mode === 'announcement'}
				canDelete={c.is_admin}
				onloadolder={() => poller.loadOlder()}
				onseen={(seq) => poller.markRead(seq)}
				onreact={react}
				ondelete={remove}
				onreport={startReport}
				onreplyprivately={(m) => replyPrivately(m.author)}
				onretry={(key) => poller.retry(key)}
				ondiscard={(key) => poller.discard(key)}
			/>
			{#if mode === 'compose'}
				<Composer onsend={(body, attachments, urgent) => poller.send(body, attachments, urgent)} onupload={(file) => poller.upload(file)} canUrgent={c.is_admin && c.mode === 'announcement'} />
			{:else}
				<div class="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-paper px-3 py-2.5 text-sm text-slate">
					{#if mode === 'parent_view'}
						<p class="flex items-center gap-2"><Eye size={16} aria-hidden="true" />{t('chat.composer.parent_view')}</p>
					{:else if mode === 'archived'}
						<p>{t('chat.composer.archived')}</p>
					{:else}
						<p>{t('chat.composer.announce_only')}</p>
						{#if replyTarget}
							<button type="button" class="btn btn-outline btn-sm gap-1.5" disabled={replyBusy} onclick={() => replyPrivately(replyTarget)}><MessageCircleReply size={15} aria-hidden="true" />{t('chat.msg.reply_privately')}</button>
						{/if}
					{/if}
				</div>
			{/if}
		{/if}
	</section>

	{#if c && showPeople && wide}
		<aside class="w-80 shrink-0 overflow-y-auto border-l border-line bg-paper/60" aria-label={t('chat.people.title')}>
			<ParticipantsPanel conversation={c} onchanged={(next) => poller.setConversation(next)} />
		</aside>
	{/if}
</div>

{#if c && !wide}
	<Modal open={showPeople} title={t('chat.people.title')} closable onclose={() => (showPeople = false)}>
		<div class="-mx-6 -mb-6"><ParticipantsPanel conversation={c} onchanged={(next) => poller.setConversation(next)} /></div>
	</Modal>
{/if}

<Modal open={!!reporting} title={t('chat.msg.report_title')} closable onclose={() => (reporting = null)}>
	{#if reportDone}
		<p class="text-sm text-ink" role="status">{t('chat.msg.report_done')}</p>
		<div class="modal-action"><button type="button" class="btn btn-primary" onclick={() => (reporting = null)}>{t('chat.close')}</button></div>
	{:else}
		<form class="grid gap-3" onsubmit={sendReport}>
			{#if reporting}<blockquote class="rounded-lg border-l-4 border-line bg-paper px-3 py-2 text-sm text-slate">{reporting.body || '…'}</blockquote>{/if}
			<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.msg.report_reason')}<textarea class="textarea min-h-24 w-full font-normal" bind:value={reason} maxlength="1000"></textarea></label>
			{#if reportError}<p class="text-sm text-danger" role="alert">{reportError}</p>{/if}
			<div class="modal-action mt-0"><button type="submit" class="btn btn-primary" disabled={reportBusy}>{t('chat.msg.report_send')}</button></div>
		</form>
	{/if}
</Modal>
