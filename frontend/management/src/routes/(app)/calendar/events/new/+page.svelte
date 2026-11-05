<script lang="ts">
	import { ArrowLeft } from '@lucide/svelte';
	import EventForm from '#lib/components/events/EventForm.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	usePageHeader(() => ({ title: t('eventform.title_new'), context: t('eventform.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/calendar"><ArrowLeft size={16} /> {t('eventform.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eventform.title_new'))}</title></svelte:head>

<EventForm mode="create" options={data.options} initial={data.initial} fields={form?.fields} message={form?.message} status={form?.status} cancelHref="/calendar" />
