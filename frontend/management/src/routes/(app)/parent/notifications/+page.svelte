<script lang="ts">
	import InboxList from '#lib/components/mobile/InboxList.svelte';
	import NotificationSettings from '#lib/components/portal/NotificationSettings.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('portal.notifications') }));
</script>

<svelte:head><title>{pageTitle(t('portal.notifications'))}</title></svelte:head>

<InboxList />
<h2 class="mt-2 font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase md:hidden">{t('portal.notif_push_title')}</h2>
<NotificationSettings settings={data.settings} />
