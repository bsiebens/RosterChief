<script lang="ts">
	import NotificationSettings from '#lib/components/portal/NotificationSettings.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('portal.notifications') }));
</script>

<svelte:head><title>{pageTitle(t('portal.notifications'))}</title></svelte:head>

<NotificationSettings settings={data.settings} />
