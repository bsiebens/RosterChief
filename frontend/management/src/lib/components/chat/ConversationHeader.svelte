<script lang="ts">
	import { ArrowLeft, BellOff, Megaphone, Users } from '@lucide/svelte';
	import type { Conversation } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import ChatAvatar from './ChatAvatar.svelte';

	/** Title, number of people and mode of the open conversation, a back button on a phone and the button for the participants. */
	let { conversation, onback, onpeople, peopleOpen = false }: { conversation: Conversation; onback?: () => void; onpeople?: () => void; peopleOpen?: boolean } = $props();
</script>

<header class="flex items-center gap-2.5 border-b border-line bg-white px-2.5 py-2 sm:px-4">
	{#if onback}
		<button type="button" class="btn btn-ghost btn-square btn-sm shrink-0 md:hidden" aria-label={t('chat.back')} onclick={onback}><ArrowLeft size={18} aria-hidden="true" /></button>
	{/if}
	<ChatAvatar {conversation} size="size-9" />
	<div class="min-w-0 flex-1">
		<h2 class="truncate font-display text-base leading-tight font-extrabold text-ink uppercase">{conversation.title}</h2>
		<p class="flex items-center gap-1.5 truncate text-xs text-muted">
			<span>{conversation.participant_count === 1 ? t('chat.header.people_one') : t('chat.header.people', { n: conversation.participant_count })}</span>
			<span aria-hidden="true">·</span>
			{#if conversation.audience === 'club'}<Megaphone size={11} aria-hidden="true" /><span>{t('chat.audience.club')}</span><span aria-hidden="true">·</span>{/if}
			<span>{t(conversation.mode === 'announcement' ? 'chat.mode.announcement' : 'chat.mode.discussion')}</span>
			{#if conversation.muted}<BellOff size={11} aria-label={t('chat.list.muted')} />{/if}
		</p>
	</div>
	{#if onpeople}
		<button type="button" class="btn btn-outline btn-sm shrink-0 gap-1.5 {peopleOpen ? 'btn-primary' : ''}" aria-pressed={peopleOpen} aria-label={peopleOpen ? t('chat.people.close') : t('chat.people.open')} onclick={onpeople}>
			<Users size={15} aria-hidden="true" /><span class="hidden sm:inline">{t('chat.header.participants')}</span>
		</button>
	{/if}
</header>
