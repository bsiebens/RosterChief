<script lang="ts">
	import ChatWorkspace from '#lib/components/chat/ChatWorkspace.svelte';
	import { chatKinds } from '#lib/chat.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('chat.title') }));
</script>

<svelte:head><title>{pageTitle(t('chat.title'))}</title></svelte:head>

<ChatWorkspace base="/member/chat" canAnnounce={data.me?.can_announce_chat === true} kinds={chatKinds(data.me, false)} />
