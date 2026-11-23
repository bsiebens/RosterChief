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

<ChatWorkspace
	base="/chat"
	desktop
	canModerate={data.me?.can_moderate_chat === true}
	canAnnounce={data.me?.can_announce_chat === true}
	kinds={chatKinds(data.me, true)}
	heightClass={data.me?.can_moderate_chat ? 'h-[calc(100dvh-14rem)] lg:h-[calc(100dvh-11.5rem)]' : 'h-[calc(100dvh-11rem)] lg:h-[calc(100dvh-9rem)]'}
/>
