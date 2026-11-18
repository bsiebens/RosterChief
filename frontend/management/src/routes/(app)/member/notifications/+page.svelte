<script lang="ts">
	import { page } from '$app/state';
	import InboxList from '#lib/components/mobile/InboxList.svelte';
	import NotificationSettings from '#lib/components/portal/NotificationSettings.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	// On a phone the bell opens the notifications only, and Me > Notifications opens the settings only (`?view=settings`); a larger screen keeps the settings here.
	const settings = $derived(page.url.searchParams.get('view') === 'settings');
	usePageHeader(() => ({ title: t(settings ? 'portal.notif_settings_title' : 'portal.notifications') }));
</script>

<svelte:head><title>{pageTitle(t('portal.notifications'))}</title></svelte:head>

{#if !settings}<InboxList />{/if}
<div class={settings ? '' : 'hidden md:block'}>
	<NotificationSettings settings={data.settings} />
</div>
