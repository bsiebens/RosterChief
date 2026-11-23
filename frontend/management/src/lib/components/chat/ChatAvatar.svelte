<script lang="ts">
	import { Megaphone } from '@lucide/svelte';
	import type { Conversation } from '#lib/chat.ts';

	/** The round picture of a conversation: a megaphone for announcements, otherwise the initial of the team, group or person. */
	let { conversation, size = 'size-11' }: { conversation: Pick<Conversation, 'title' | 'mode' | 'kind' | 'muted'>; size?: string } = $props();
	const initial = $derived((conversation.title.trim()[0] ?? '?').toUpperCase());
</script>

<span class="{size} flex shrink-0 items-center justify-center rounded-full font-display text-base font-extrabold {conversation.mode === 'announcement' ? 'bg-club/15 text-club' : conversation.kind === 'team' ? 'bg-club text-club-content' : 'bg-ink/10 text-ink'} {conversation.muted ? 'opacity-60' : ''}" aria-hidden="true">
	{#if conversation.mode === 'announcement'}<Megaphone size={19} />{:else}{initial}{/if}
</span>
