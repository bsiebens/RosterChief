<script lang="ts">
	import { goto } from '$app/navigation';
	import { Inbox, ChevronRight } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { registrationHref, statusBadge } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const href = (patch: Partial<{ q: string; status: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.status !== 'awaiting') sp.set('status', f.status);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const filtered = $derived(!!data.filters.q);
	const tabList = $derived([
		{ key: 'awaiting', label: t('reg.tab.awaiting'), count: data.awaiting, warn: true },
		{ key: 'confirmed', label: t('reg.tab.confirmed') },
		{ key: 'cancelled', label: t('reg.tab.cancelled') },
		{ key: 'all', label: t('reg.tab.all') }
	]);

	usePageHeader(() => ({ title: t('reg.title'), context: t('reg.awaiting_n', { n: data.awaiting }) }));
</script>

<svelte:head><title>{pageTitle(t('reg.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('reg.search')} searchPlaceholder={t('reg.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => navigate(href({ q: '' }))}>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.status} label={t('reg.tabs')} hrefFor={(key) => href({ status: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Inbox size={28} />
				<p>{filtered ? t('reg.empty_filtered') : data.filters.status === 'awaiting' ? t('reg.empty_awaiting') : t('reg.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('reg.col.contact')}</th><th class="hidden lg:table-cell">{t('reg.col.season')}</th><th class="hidden text-right lg:table-cell">{t('reg.col.people')}</th><th class="text-right">{t('reg.col.total')}</th><th class="hidden xl:table-cell">{t('reg.col.invoice')}</th><th class="hidden md:table-cell">{t('reg.col.submitted')}</th><th><span class="sr-only">{t('reg.col.actions')}</span></th></tr>
					</thead>
					<tbody>
						{#each data.rows as r (r.id)}
							<tr class="hover:bg-paper">
								<td>
									<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
										<a class="link font-semibold" href={registrationHref(r.id)}>{r.contact?.name ?? t('reg.no_contact')}</a>
										<span class="badge badge-sm {statusBadge(r.status)}">{t(`reg.status.${r.status}`)}</span>
									</div>
									{#if r.contact?.email}<p class="text-xs text-muted">{r.contact.email}</p>{/if}
								</td>
								<td class="hidden text-sm lg:table-cell">{r.season_name}</td>
								<td class="hidden text-right font-mono text-sm lg:table-cell">{r.people_count}</td>
								<td class="text-right font-mono text-sm">{money(r.total)}</td>
								<td class="hidden font-mono text-xs xl:table-cell">{r.invoice_number ?? '–'}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap text-muted md:table-cell">{formatDay(r.submitted_at)}</td>
								<td class="text-right whitespace-nowrap"><a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href={registrationHref(r.id)} aria-label={t('reg.edit_named', { name: r.contact?.name ?? t('reg.no_contact') })} title={t('common.edit')}><ChevronRight size={18} /></a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
