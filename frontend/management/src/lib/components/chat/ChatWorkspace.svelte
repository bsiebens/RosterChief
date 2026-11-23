<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { MessageSquare, Plus } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import type { Conversation, ConversationKind } from '#lib/chat.ts';
	import { chatApi } from '#lib/chat.ts';
	import { chatList } from '#lib/chat.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
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
	const tab = $derived(canModerate && page.url.searchParams.get('tab') === 'moderation' ? 'moderation' : 'chats');
	let reportCount = $state(0);
	let settingsOpen = $state(false);
	let notice = $state('');
	const tabList = $derived([
		{ key: 'chats', label: t('chat.tab.chats') },
		{ key: 'moderation', label: t('chat.tab.moderation'), count: reportCount, warn: true }
	]);
	let creating = $state(false);

	onMount(() => {
		if (canModerate)
			void chatApi.reports().then((res) => {
				if (res.ok && res.data) reportCount = res.data.results.filter((r) => !r.handled).length;
			});
		return chatList.acquire();
	});

	const hrefFor = (id: string) => `${base}?c=${id}`;
	const open = (c: Conversation) => {
		chatList.upsert(c);
		void goto(hrefFor(c.id), { reset: false });
	};
</script>

{#snippet tabBar()}
	<ListFilterBar>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={tab} label={t('chat.title')} hrefFor={(key) => (key === 'chats' ? base : `${base}?tab=${key}`)} onnavigate={(h) => goto(h, { reset: false })} />
			<button type="button" class="btn btn-outline btn-sm" onclick={() => (settingsOpen = true)}>{t('chat.tab.settings')}</button>
		{/snippet}
	</ListFilterBar>
{/snippet}

<div class={canModerate ? '-mx-7 -my-6 flex flex-col' : ''}>
{#if canModerate}{@render tabBar()}{/if}
<div class={canModerate ? 'px-7 py-6' : ''}>
{#if notice}<div class="mb-4"><NoticeAlert message={notice} onhide={() => (notice = '')} /></div>{/if}
{#if tab === 'moderation'}
	<ModerationPanel {heightClass} bind:openCount={reportCount} />
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
</div>
</div>

{#if canModerate}
	<Modal bind:open={settingsOpen} title={t('chat.settings.title')} closable onclose={() => (settingsOpen = false)}>
		{#if settingsOpen}<SettingsPanel onsaved={() => { settingsOpen = false; notice = t('chat.settings.saved'); }} />{/if}
	</Modal>
{/if}
