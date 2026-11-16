<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { goto } from '$app/navigation';
	import { Upload, UserPlus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import FilterStrip from '#lib/components/members/list/FilterStrip.svelte';
	import MembersTable from '#lib/components/members/list/MembersTable.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { PAGE_SIZE, errorKey, pageHref } from '#lib/members/list.ts';

	let { data } = $props();

	usePageHeader(() => ({
		title: t('members.list.title'),
		context: data.error ? '' : data.count === 1 ? t('members.list.count_one') : t('members.list.count', { n: data.count }),
		actions: data.canManage ? addButton : null
	}));

	const navigate = (href: string) => goto(href, { reset: false });

	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType(q: string) {
		clearTimeout(timer);
		timer = setTimeout(() => {
			if (q.trim() !== data.filters.q) goto(pageHref(data.filters, { q: q.trim() }), { reset: false, replace: true });
		}, 400);
	}
</script>

{#snippet addButton()}
	<a class="btn btn-outline gap-2" href="/members/import"><Upload size={16} /> {t('import.button')}</a>
	<a class="btn btn-primary gap-2" href="/members/new"><UserPlus size={16} /> {t('members.list.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('members.list.title'))}</title></svelte:head>

<!-- Full-bleed: cancels the layout's px-7/py-6 so the filter strip and table reach the edges. -->
<div class="-mx-7 -my-6 flex flex-col">
<FilterStrip filters={data.filters} teams={data.teams} seasons={data.seasons} showFees={data.showFees} onnavigate={navigate} ontype={onType} />

<div class="px-6 py-4">
	{#if data.error}
		<Alert kind="error">{t(errorKey(data.error.status))}</Alert>
	{:else}
		<MembersTable rows={data.rows} filters={data.filters} canManage={data.canManage} showFees={data.showFees} onnavigate={navigate} />
		{#if data.count > 0}
			<div class="mt-3 flex items-center gap-2 text-xs text-muted">
				<span class="font-mono">{t('members.list.range', { from: data.filters.offset + 1, to: Math.min(data.count, data.filters.offset + PAGE_SIZE), n: data.count })}</span>
				<span class="flex-1"></span>
				<a class="btn btn-sm btn-outline gap-1 {data.filters.offset <= 0 ? 'pointer-events-none opacity-45' : ''}" href={pageHref(data.filters, { offset: Math.max(0, data.filters.offset - PAGE_SIZE) })} aria-disabled={data.filters.offset <= 0}><ChevronLeft size={14} /> {t('common.previous')}</a>
				<a class="btn btn-sm btn-outline gap-1 {data.filters.offset + PAGE_SIZE >= data.count ? 'pointer-events-none opacity-45' : ''}" href={pageHref(data.filters, { offset: data.filters.offset + PAGE_SIZE })} aria-disabled={data.filters.offset + PAGE_SIZE >= data.count}>{t('common.next')} <ChevronRight size={14} /></a>
			</div>
		{/if}
	{/if}
</div>
</div>
