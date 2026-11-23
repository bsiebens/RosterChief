<script lang="ts">
	import { Ellipsis, Flame, Flag, MessageCircleReply, RotateCcw, Trash2, TriangleAlert } from '@lucide/svelte';
	import { bubbleClass, isImage, showRoleLabel, timeLabel, type Message } from '#lib/chat.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import AttachmentChip from './AttachmentChip.svelte';
	import PersonAvatar from './PersonAvatar.svelte';
	import ReactionBar from './ReactionBar.svelte';

	/**
	 * One message. Mine on the right, the others on the left with their name (and, for staff and admins, their role); the colour follows the
	 * author's role. `state` is set while the message is still on its way. Every action is optional: leave a callback out and the action is not offered.
	 */
	let {
		message, status, error = '', showName = true, canDelete = false, announcement = false, myRole = 'other',
		onreact, ondelete, onreport, onreplyprivately, onretry, ondiscard
	}: {
		message: Message;
		status?: 'sending' | 'failed';
		error?: string;
		showName?: boolean;
		/** I am a chat admin: I may delete other people's messages. */
		canDelete?: boolean;
		announcement?: boolean;
		myRole?: Message['author']['role'];
		onreact?: (message: Message, emoji: string) => void;
		ondelete?: (message: Message) => void;
		onreport?: (message: Message) => void;
		onreplyprivately?: (message: Message) => void;
		onretry?: () => void;
		ondiscard?: () => void;
	} = $props();

	const mine = $derived(message.mine);
	const role = $derived(mine && message.author.role === 'other' ? myRole : message.author.role);
	const images = $derived(message.attachments.filter(isImage));
	const files = $derived(message.attachments.filter((a) => !isImage(a)));
	const canReport = $derived(!mine && !message.deleted && !!onreport);
	const canRemove = $derived((mine || canDelete) && !message.deleted && !!ondelete && !status);
	const canReply = $derived(!mine && !message.deleted && announcement && !!onreplyprivately);
	const hasMenu = $derived(canReport || canRemove || canReply);

	let menuOpen = $state(false);
	let menu: HTMLElement | undefined = $state();
</script>

<svelte:window onclick={(e) => { if (menuOpen && menu && !menu.contains(e.target as Node)) menuOpen = false; }} onkeydown={(e) => { if (menuOpen && e.key === 'Escape') menuOpen = false; }} />

<div class="flex items-end gap-2 {mine ? 'flex-row-reverse' : ''}">
	{#if !mine}
		<div class="mb-5 w-8 shrink-0">{#if showName}<PersonAvatar person={message.author} />{/if}</div>
	{/if}
	<div class="flex min-w-0 max-w-[82%] flex-col {mine ? 'items-end' : 'items-start'}">
		{#if !mine && showName}
			<p class="mb-0.5 max-w-full truncate px-1 text-xs text-muted"><b class="font-semibold text-ink">{message.author.name}</b>{#if showRoleLabel(message.author)} <span class="text-muted">· {message.author.role_label}</span>{/if}</p>
		{/if}
		<div class="group flex max-w-full items-start gap-1 {mine ? 'flex-row-reverse' : ''}">
			<div class="min-w-0 rounded-2xl px-3.5 py-2 text-sm break-words shadow-sm {message.deleted ? 'border border-line bg-white text-muted italic' : bubbleClass(role)} {mine ? 'rounded-br-sm' : 'rounded-bl-sm'} {status === 'sending' ? 'opacity-70' : ''} {status === 'failed' ? 'ring-2 ring-danger' : ''}">
				{#if message.deleted}
					<p>{t('chat.msg.deleted')}</p>
				{:else}
					{#if message.urgent}<p class="mb-1 inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-0.5 text-[11px] font-bold tracking-wide text-danger uppercase"><Flame size={12} aria-hidden="true" />{t('chat.msg.urgent')}</p>{/if}
					{#if message.body}<p class="whitespace-pre-wrap">{message.body}</p>{/if}
					{#if images.length}
						<div class="mt-1.5 flex flex-wrap gap-1.5">
							{#each images as image (image.id)}
								<a href={image.url} target="_blank" rel="noopener" aria-label={t('chat.msg.open_image', { name: image.name })}><img class="max-h-44 max-w-full rounded-lg bg-white object-cover" src={image.url} alt={image.name} loading="lazy" /></a>
							{/each}
						</div>
					{/if}
					{#if files.length}
						<div class="mt-1.5 flex flex-col gap-1.5">{#each files as file (file.id)}<AttachmentChip attachment={file} />{/each}</div>
					{/if}
				{/if}
			</div>
			{#if hasMenu}
				<div bind:this={menu} class="relative shrink-0">
					<button type="button" class="grid size-7 place-items-center rounded-full text-muted hover:bg-paper hover:text-ink" aria-label={t('chat.msg.menu')} aria-haspopup="menu" aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}><Ellipsis size={16} aria-hidden="true" /></button>
					{#if menuOpen}
						<div class="absolute z-20 mt-1 w-48 rounded-lg border border-line bg-white py-1 shadow-lg {mine ? 'right-0' : 'left-0'}" role="menu">
							{#if canReply}<button type="button" role="menuitem" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink hover:bg-paper" onclick={() => { menuOpen = false; onreplyprivately?.(message); }}><MessageCircleReply size={15} aria-hidden="true" />{t('chat.msg.reply_privately')}</button>{/if}
							{#if canReport}<button type="button" role="menuitem" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink hover:bg-paper" onclick={() => { menuOpen = false; onreport?.(message); }}><Flag size={15} aria-hidden="true" />{t('chat.msg.report')}</button>{/if}
							{#if canRemove}<button type="button" role="menuitem" class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-danger hover:bg-paper" onclick={() => { menuOpen = false; ondelete?.(message); }}><Trash2 size={15} aria-hidden="true" />{t('chat.msg.delete')}</button>{/if}
						</div>
					{/if}
				</div>
			{/if}
		</div>
		{#if !message.deleted && !status && (message.reactions.length || onreact)}
			<ReactionBar reactions={message.reactions} onreact={onreact ? (emoji) => onreact(message, emoji) : undefined} align={mine ? 'end' : 'start'} />
		{/if}
		<p class="mt-0.5 flex items-center gap-1.5 px-1 font-mono text-[10px] text-muted">
			{#if status === 'failed'}
				<span class="inline-flex items-center gap-1 font-sans text-xs font-semibold text-danger" role="alert"><TriangleAlert size={12} aria-hidden="true" />{t('chat.msg.failed')}{#if error}: {error}{/if}</span>
				<button type="button" class="inline-flex items-center gap-1 font-sans text-xs font-bold text-ink underline" onclick={onretry}><RotateCcw size={11} aria-hidden="true" />{t('chat.msg.retry')}</button>
				<button type="button" class="font-sans text-xs text-muted underline" onclick={ondiscard}>{t('chat.msg.discard')}</button>
			{:else if status === 'sending'}
				{t('chat.msg.sending')}
			{:else}
				{timeLabel(message.created, getLang())}{#if message.edited && !message.deleted} · {t('chat.msg.edited')}{/if}{#if mine} · {t('chat.msg.sent')}{/if}
			{/if}
		</p>
	</div>
</div>
