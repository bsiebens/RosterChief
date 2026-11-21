<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ClipboardList, Eye } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import SendStatusBadge from '#lib/components/forms/SendStatusBadge.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { sendHref } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const href = (patch: Partial<{ tab: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.tab !== 'open') sp.set('tab', f.tab);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const tabList = $derived([
		{ key: 'open', label: t('forms.sends.tab.open') },
		{ key: 'scheduled', label: t('forms.sends.tab.scheduled') },
		{ key: 'closed', label: t('forms.sends.tab.closed') },
		{ key: 'all', label: t('forms.sends.tab.all') }
	]);
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');

	usePageHeader(() => ({ title: t('forms.sends.title'), context: data.count === 1 ? t('forms.sends.count_one') : t('forms.sends.count', { n: data.count }) }));
</script>

<svelte:head><title>{pageTitle(t('forms.sends.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.tab} label={t('forms.sends.tabs')} hrefFor={(key) => href({ tab: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>
	<div class="grid gap-4 px-6 py-4">
		{#if deleted}<Alert kind="success">{t('forms.sends.deleted')}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<ClipboardList size={28} />
				<p>{t('forms.sends.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('forms.sends.col.form')}</th><th class="hidden lg:table-cell">{t('forms.sends.col.audience')}</th><th class="hidden xl:table-cell">{t('forms.sends.col.window')}</th><th>{t('forms.sends.col.status')}</th><th class="text-right">{t('forms.sends.col.responded')}</th><th><span class="sr-only">{t('forms.col.actions')}</span></th></tr>
					</thead>
					<tbody>
						{#each data.rows as s (s.id)}
							<tr class="hover:bg-paper">
								<td><a class="link font-semibold" href={sendHref(s.id)}>{s.form_title}</a></td>
								<td class="hidden text-sm text-muted lg:table-cell">{s.audience_label}</td>
								<td class="hidden font-mono text-xs text-muted xl:table-cell">
									{#if s.opens_at}<div>{t('forms.sends.from', { date: formatDateTime(s.opens_at) })}</div>{/if}
									{#if s.closes_at}<div>{t('forms.sends.until', { date: formatDateTime(s.closes_at) })}</div>{/if}
									{#if !s.opens_at && !s.closes_at}{t('common.none')}{/if}
								</td>
								<td><SendStatusBadge status={s.status} small /></td>
								<td class="text-right font-mono text-sm whitespace-nowrap">{t('forms.sends.x_of_y', { x: s.response_count, y: s.audience_count })}</td>
								<td class="text-right"><div class="flex items-center justify-end gap-1"><a class="btn btn-outline btn-xs gap-1" href={sendHref(s.id)} aria-label={t('forms.sends.open_named', { title: s.form_title })}><Eye size={13} /> <span class="sr-only xl:not-sr-only">{t('forms.sends.responses')}</span></a></div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
