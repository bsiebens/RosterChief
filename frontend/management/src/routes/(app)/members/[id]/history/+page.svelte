<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { ArrowLeft } from '@lucide/svelte';
	import HistoryTable from '#lib/components/members/detail/HistoryTable.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: `${t('members.history.title')} — ${data.name}`, context: t('members.history.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/members/{data.id}"><ArrowLeft size={16} /> {t('members.history.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(`${t('members.history.title')} · ${data.name}`)}</title></svelte:head>

<div class="card">
	<HistoryTable items={data.items} />
	<Pager count={data.count} offset={data.offset} limit={data.limit} href={(o) => `/members/${data.id}/history${o ? `?offset=${o}` : ''}`} />
</div>
