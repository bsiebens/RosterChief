<script lang="ts">
		import { goto } from '$app/navigation';
	import { FileDown, FileText } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import InvoiceOutcomeNote from '#lib/components/shop/InvoiceOutcomeNote.svelte';
	import InvoiceRecreate from '#lib/components/shop/InvoiceRecreate.svelte';
	import type { InvoiceOutcome } from '#lib/shop-orders.ts';
	import { day, invoicePdfHref, money, orderHref, paymentBadge } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	// The result of rebuilding a PDF, per invoice, shown on its own line under that row's buttons.
	let outcomes = $state<Record<string, InvoiceOutcome | null>>({});
	type Patch = Partial<{ q: string; status: string; from: string; to: string; offset: number }>;
	const href = (patch: Patch) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.status !== 'all') sp.set('status', f.status);
		if (f.from) sp.set('from', f.from);
		if (f.to) sp.set('to', f.to);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.status !== 'all' || !!data.filters.from || !!data.filters.to);
	const filterTabs = $derived(['all', 'unpaid', 'overdue', 'paid'].map((key) => ({ key, label: t(`shop.invc.tab.${key}`) })));

	usePageHeader(() => ({ title: t('shop.invc.title'), context: data.count === 1 ? t('shop.invc.count_one') : t('shop.invc.count', { n: data.count }) }));
</script>

<svelte:head><title>{pageTitle(t('shop.invc.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.invc.search')} searchPlaceholder={t('shop.invc.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		<label class="flex items-center gap-2 text-xs text-muted">{t('shop.invc.from')}
			<input type="date" class="input w-auto" value={data.filters.from} onchange={(e) => goto(href({ from: e.currentTarget.value }), { replace: true, reset: false })} />
		</label>
		<label class="flex items-center gap-2 text-xs text-muted">{t('shop.invc.to')}
			<input type="date" class="input w-auto" value={data.filters.to} onchange={(e) => goto(href({ to: e.currentTarget.value }), { replace: true, reset: false })} />
		</label>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.status} label={t('shop.orders.filter_label')} hrefFor={(key) => href({ status: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted"><FileText size={28} /><p>{filtered ? t('shop.invc.empty_filtered') : t('shop.invc.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('shop.invc.col.number')}</th><th class="hidden lg:table-cell">{t('shop.orders.col.order')}</th><th>{t('shop.orders.col.purchaser')}</th><th class="hidden xl:table-cell">{t('shop.invc.col.issued')}</th><th class="hidden xl:table-cell">{t('shop.invc.col.due')}</th>
							<th class="hidden text-right lg:table-cell">{t('shop.orders.col.total')}</th><th class="text-right">{t('shop.orders.col.due')}</th><th>{t('shop.invc.col.state')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as i (i.id)}
							<tr class="hover:bg-paper {data.canOrders ? 'cursor-pointer' : ''}" onclick={(e) => { if (data.canOrders && !(e.target as HTMLElement).closest('a, button, input, label, select')) goto(orderHref(i.order.id)); }}>
								<td class="font-mono text-xs font-semibold">{i.number}</td>
								<td class="hidden lg:table-cell">{#if data.canOrders}<a class="link font-mono text-xs" href={orderHref(i.order.id)}>{i.order.number}</a>{:else}<span class="font-mono text-xs">{i.order.number}</span>{/if}</td>
								<td class="font-semibold">{i.purchaser.name}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap text-muted xl:table-cell">{day(i.issued_at)}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap xl:table-cell {i.is_overdue ? 'font-semibold text-error' : 'text-muted'}">{i.due_date ? day(i.due_date) : '—'}</td>
								<td class="hidden text-right font-mono text-sm lg:table-cell">{money(i.total)}</td>
								<td class="text-right font-mono text-sm">{money(i.amount_due)}</td>
								<td>
									<span class="badge badge-sm {paymentBadge(i.payment_status)}">{t(`shop.orders.payment.${i.payment_status}`)}</span>
									{#if i.is_overdue}<span class="badge badge-sm badge-error ml-1">{t('shop.invc.overdue')}</span>{/if}
								</td>
								<td class="text-right whitespace-nowrap">
									<div class="flex flex-wrap items-center justify-end gap-1">
										<a class="btn btn-outline btn-xs gap-1" href={invoicePdfHref(i.id)} download="{i.number}.pdf" target="_blank" rel="noopener"><FileDown size={13} /> {t('shop.invc.pdf')}</a>
										<InvoiceRecreate invoiceId={i.id} size="xs" onoutcome={(o) => (outcomes[i.id] = o)} />
										{#if data.canOrders}<a class="btn btn-outline btn-xs" href={orderHref(i.order.id)}>{t('shop.invc.open_order')}</a>{/if}
									</div>
									{#if outcomes[i.id]}<div class="mt-1 flex justify-end"><InvoiceOutcomeNote outcome={outcomes[i.id]!} /></div>{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
