<script lang="ts">
	import { BellOff, Paperclip, Search } from '@lucide/svelte';
	import { listStamp, type Conversation } from '#lib/chat.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import ChatAvatar from './ChatAvatar.svelte';

	/**
	 * The conversations, newest activity first (the order comes from the store). Unread chats are bold with a count and a coloured left bar,
	 * muted ones greyed, announcement chats get a megaphone. Each row is a link (`hrefFor`), so the browser's back button works on a phone.
	 */
	let { conversations, selectedId = '', loaded = true, error = '', hrefFor, onretry }: {
		conversations: Conversation[];
		selectedId?: string;
		loaded?: boolean;
		error?: string;
		hrefFor: (id: string) => string;
		onretry?: () => void;
	} = $props();

	let q = $state('');
	const lang = $derived(getLang());
	const shown = $derived(q.trim() ? conversations.filter((c) => c.title.toLocaleLowerCase().includes(q.trim().toLocaleLowerCase())) : conversations);

	function preview(c: Conversation): string {
		const m = c.last_message;
		if (!m) return t('chat.list.no_messages');
		if (m.deleted) return t('chat.list.deleted');
		const text = m.body.trim() || (m.attachments.length ? t('chat.list.attachment') : '');
		return c.kind === 'direct' && !m.mine ? text : `${m.mine ? t('chat.list.you') : m.author.name.split(' ')[0]}: ${text}`;
	}
</script>

<div class="flex min-h-0 flex-1 flex-col">
	{#if conversations.length > 6}
		<div class="relative border-b border-line p-2">
			<Search size={14} class="pointer-events-none absolute top-1/2 left-4.5 -translate-y-1/2 text-muted" aria-hidden="true" />
			<input class="input input-sm w-full pl-8" type="search" bind:value={q} placeholder={t('chat.list.search')} aria-label={t('chat.list.search')} />
		</div>
	{/if}
	{#if !loaded && !error}
		<p class="p-6 text-center text-sm text-muted" role="status">{t('chat.loading')}</p>
	{:else if error && !loaded}
		<div class="grid justify-items-center gap-2 p-6 text-center text-sm" role="alert"><p class="text-danger">{error}</p>{#if onretry}<button type="button" class="btn btn-outline btn-sm" onclick={onretry}>{t('chat.retry')}</button>{/if}</div>
	{:else if !shown.length}
		<div class="p-8 text-center"><p class="font-semibold text-ink">{t('chat.list.empty')}</p><p class="mt-1 text-sm text-muted">{t('chat.list.empty_help')}</p></div>
	{:else}
		<ul class="min-h-0 flex-1 divide-y divide-rule overflow-y-auto" aria-label={t('chat.list.label')}>
			{#each shown as c (c.id)}
				<li>
					<a class="flex items-center gap-3 px-3 py-3 no-underline hover:bg-paper {selectedId === c.id ? 'bg-paper' : ''} {c.unread && !c.muted ? 'shadow-[inset_4px_0_0_var(--color-club)]' : ''} {c.muted ? 'opacity-70' : ''}" href={hrefFor(c.id)} aria-current={selectedId === c.id ? 'page' : undefined} data-sveltekit-noscroll>
						<ChatAvatar conversation={c} />
						<span class="min-w-0 flex-1">
							<span class="flex items-baseline gap-2">
								<b class="min-w-0 flex-1 truncate text-ink {c.unread ? 'font-extrabold' : 'font-semibold'} {c.muted ? 'text-muted' : ''}">{c.title}</b>
								{#if c.muted}<BellOff size={12} class="shrink-0 text-muted" aria-label={t('chat.list.muted')} />{/if}
								<span class="shrink-0 font-mono text-[10px] text-muted">{listStamp(c.last_message?.created ?? c.updated, lang)}</span>
							</span>
							<span class="mt-0.5 flex items-center gap-2">
								<span class="min-w-0 flex-1 truncate text-xs {c.unread ? 'font-semibold text-ink' : 'text-muted'}">{#if c.last_message?.attachments.length && !c.last_message.deleted}<Paperclip size={11} class="mr-0.5 inline" aria-hidden="true" />{/if}{preview(c)}</span>
								{#if c.unread > 0}<span class="inline-flex min-w-5 shrink-0 items-center justify-center rounded-full {c.muted ? 'bg-muted' : 'bg-club'} px-1.5 text-[11px] leading-5 font-bold text-club-content" aria-label={t('chat.list.unread', { n: c.unread })}>{c.unread > 99 ? '99+' : c.unread}</span>{/if}
							</span>
						</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>
