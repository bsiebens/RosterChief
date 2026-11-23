<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { MessageSquare, Plus } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import type { Conversation, ConversationKind } from '#lib/chat.ts';
	import { chatList } from '#lib/chat.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import ConversationList from './ConversationList.svelte';
	import ConversationView from './ConversationView.svelte';
	import ModerationPanel from './ModerationPanel.svelte';
	import SettingsPanel from './SettingsPanel.svelte';
	import NewChatSheet from './NewChatSheet.svelte';

	/**
	 * The whole chat screen, shared by the member app, the coach app and the management app: the list, the open conversation and (inside it)
	 * the participants. The open conversation is the `?c=` of the URL, so the browser's back button closes it on a phone. Under 768 px one
	 * pane shows at a time; from there the list and the conversation sit side by side.
	 */
	let { base, canAnnounce = false, kinds = ['team', 'group', 'direct'], canModerate = false, desktop = false, heightClass = 'h-[calc(100dvh-11rem)] md:h-[calc(100dvh-8rem)]' }: {
		/** The path of the page, e.g. `/member/chat`. */
		base: string;
		canAnnounce?: boolean;
		kinds?: ConversationKind[];
		/** Show the Moderation tab (management app only). */
		canModerate?: boolean;
		/** The management layout: the participants open next to the messages on wide screens. */
		desktop?: boolean;
		heightClass?: string;
	} = $props();

	const selected = $derived(page.url.searchParams.get('c') ?? '');
	const tab = $derived(canModerate && page.url.searchParams.get('tab') === 'moderation' ? 'moderation' : canModerate && page.url.searchParams.get('tab') === 'settings' ? 'settings' : 'chats');
	let creating = $state(false);

	onMount(() => chatList.acquire());

	const hrefFor = (id: string) => `${base}?c=${id}`;
	const open = (c: Conversation) => {
		chatList.upsert(c);
		void goto(hrefFor(c.id), { reset: false });
	};
</script>

{#if canModerate}
	<div class="mb-3 flex w-fit flex-wrap items-center gap-1.5" role="group" aria-label={t('chat.title')}>
		<a class="portal-person compact text" href={base} aria-current={tab === 'chats' ? 'true' : undefined}>{t('chat.tab.chats')}</a>
		<a class="portal-person compact text" href="{base}?tab=moderation" aria-current={tab === 'moderation' ? 'true' : undefined}>{t('chat.tab.moderation')}</a>
		<a class="portal-person compact text" href="{base}?tab=settings" aria-current={tab === 'settings' ? 'true' : undefined}>{t('chat.tab.settings')}</a>
	</div>
{/if}

{#if tab === 'moderation'}
	<ModerationPanel {heightClass} />
{:else if tab === 'settings'}
	<SettingsPanel />
{:else}
	<div class="flex overflow-hidden rounded-xl border border-line bg-white {heightClass}">
		<aside class="{selected ? 'hidden md:flex' : 'flex'} w-full shrink-0 flex-col border-r border-line md:w-80 xl:w-[22rem]" aria-label={t('chat.list.label')}>
			<div class="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
				<h2 class="font-display text-lg font-extrabold text-ink uppercase">{t('chat.title')}{#if chatList.unreadTotal > 0} <span class="ml-1 rounded-full bg-club px-2 py-0.5 align-middle font-sans text-xs text-club-content">{chatList.unreadTotal}</span>{/if}</h2>
				<button type="button" class="btn btn-primary btn-sm gap-1.5" onclick={() => (creating = true)}><Plus size={15} aria-hidden="true" />{t('chat.new')}</button>
			</div>
			<ConversationList conversations={chatList.conversations} selectedId={selected} loaded={chatList.loaded} error={chatList.error} {hrefFor} onretry={() => chatList.refresh()} />
		</aside>
		<div class="{selected ? 'block' : 'hidden md:block'} min-w-0 flex-1">
			{#if selected}
				{#key selected}
					<ConversationView id={selected} onback={() => goto(base, { reset: false })} onopen={open} peopleDefault={desktop} />
				{/key}
			{:else}
				<div class="grid h-full place-items-center p-6 text-center text-sm text-muted"><div class="grid justify-items-center gap-2"><MessageSquare size={32} aria-hidden="true" /><p>{t('chat.select')}</p></div></div>
			{/if}
		</div>
	</div>
	<NewChatSheet bind:open={creating} {canAnnounce} {kinds} oncreated={open} />
{/if}
