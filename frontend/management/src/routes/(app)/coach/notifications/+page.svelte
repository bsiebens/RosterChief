<script lang="ts">
	import InboxList from '#lib/components/mobile/InboxList.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	// The coach side's bell opens the same notification list as the member side (on a phone; larger screens keep the bell's drop-down).
	usePageHeader(() => ({ title: t('portal.notifications') }));
</script>

<svelte:head><title>{pageTitle(t('portal.notifications'))}</title></svelte:head>

<InboxList portal="coach" />
