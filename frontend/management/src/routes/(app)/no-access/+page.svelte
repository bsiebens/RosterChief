<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { signOut } from '#lib/session.ts';
	import Alert from '#lib/components/Alert.svelte';

	let { data } = $props();
	usePageHeader(() => ({ title: t('home.no_access.heading') }));
</script>

<svelte:head><title>{pageTitle(t('home.no_access.title'))}</title></svelte:head>

<div class="max-w-xl">
	<Alert kind="warning" title={t('home.no_access.title')}>{t('home.no_access.body', { club: data.branding?.name ?? '' })}</Alert>
	<button class="btn btn-outline mt-4" onclick={signOut}>{t('shell.menu.logout')}</button>
</div>
