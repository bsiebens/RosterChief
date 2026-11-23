<script lang="ts">
		import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { CircleCheck, Factory, PackageCheck, Plus, ShoppingBag, TriangleAlert, Truck, ChevronRight } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import OrderBadges from '#lib/components/shop/OrderBadges.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, MANUAL_METHODS, money, orderHref } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; tab: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.tab !== 'all') sp.set('tab', f.tab);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.tab !== 'all');
	const TAB_KEYS = ['all', 'unpaid', 'partial', 'to_fulfil', 'ready_to_confirm', 'ready', 'completed', 'cancelled'];
	const filterTabs = $derived(TAB_KEYS.map((key) => ({ key, label: t(`shop.orders.tab.${key}`), count: data.counts[key as keyof typeof data.counts], warn: key === 'ready_to_confirm' })));

	const highlight = (o: { ready_to_confirm: boolean; fulfillment_status: string; is_closed: boolean }) => o.ready_to_confirm && o.fulfillment_status !== 'cancelled' && !o.is_closed;

	let selected = $state<string[]>([]);
	// A new page of rows starts without a selection.
	$effect(() => {
		data.rows;
		selected = [];
	});
	const selectable = $derived(data.rows.filter((o) => o.fulfillment_status !== 'cancelled').map((o) => o.id));
	const allOn = $derived(selectable.length > 0 && selectable.every((id) => selected.includes(id)));
	const toggleAll = () => (selected = allOn ? [] : [...selectable]);

	let busy = $state(false);
	let paidOpen = $state(false);
	let method = $state('cash');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const doneCount = $derived(form && 'count' in form ? Number(form.count) : 0);
	const failure = $derived(form && 'op' in form && !('done' in form) ? String(form.message || t('common.error.generic')) : '');

	usePageHeader(() => ({ title: t('shop.orders.title'), context: data.count === 1 ? t('shop.orders.count_one') : t('shop.orders.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/production"><Factory size={16} /> {t('shop.nav.production')}</a>
	<a class="btn btn-outline gap-2" href="/shop/pickup"><PackageCheck size={16} /> {t('shop.orders.go_pickup')}</a>
	<a class="btn btn-outline gap-2" href="/shop/delivery"><Truck size={16} /> {t('shop.orders.go_delivery')}</a>
	<a class="btn btn-primary gap-2" href="/shop/orders/new"><Plus size={16} /> {t('shop.orders.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.orders.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.orders.search')} searchPlaceholder={t('shop.orders.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.tab} label={t('shop.orders.filter_label')} hrefFor={(key) => href({ tab: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if data.stats}
			<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
				<div class="card p-4"><div class="text-[13px] text-muted">{t('shop.orders.kpi.open')}</div><div class="font-mono text-2xl font-semibold text-ink">{data.stats.orders_open}</div></div>
				<div class="card p-4"><div class="text-[13px] text-muted">{t('shop.orders.kpi.total')}</div><div class="font-mono text-2xl font-semibold text-ink">{data.stats.orders_total}</div></div>
				<div class="card p-4"><div class="text-[13px] text-muted">{t('shop.orders.kpi.revenue')}</div><div class="font-mono text-2xl font-semibold text-ink">{money(data.stats.revenue)}</div></div>
				<div class="card p-4"><div class="text-[13px] text-muted">{t('shop.orders.kpi.outstanding')}</div><div class="font-mono text-2xl font-semibold text-ink">{money(data.stats.outstanding)}</div></div>
			</div>
		{/if}
		{#if data.counts.ready_to_confirm > 0 && data.filters.tab !== 'ready_to_confirm'}
			<div class="alert alert-warning flex-wrap items-center" role="status">
				<TriangleAlert size={18} class="shrink-0" aria-hidden="true" />
				<span class="min-w-0 flex-1 text-sm">{data.counts.ready_to_confirm === 1 ? t('shop.orders.ready_hint_one') : t('shop.orders.ready_hint', { n: data.counts.ready_to_confirm })}</span>
				<a class="btn btn-outline btn-sm shrink-0 gap-2" href="/shop/pickup"><PackageCheck size={14} /> {t('shop.orders.open_pickup')}</a>
			</div>
		{/if}
		{#if done === 'markPaid'}<Alert kind="success">{t('shop.orders.bulk.paid_done', { n: doneCount })}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<ShoppingBag size={28} />
				<p>{filtered ? t('shop.orders.empty_filtered') : t('shop.orders.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
					<span class="font-display text-sm font-bold tracking-wide text-muted uppercase">{selected.length ? t('shop.orders.selected', { n: selected.length }) : data.count === 1 ? t('shop.orders.count_one') : t('shop.orders.count', { n: data.count })}</span>
					<div class="flex gap-2">
						<button class="btn btn-success btn-sm gap-2" type="button" disabled={!selected.length} onclick={() => (paidOpen = true)}><CircleCheck size={14} /> {t('shop.orders.bulk.mark_paid')}</button>
					</div>
				</div>
				<table class="table">
					<thead>
						<tr>
							<th><input type="checkbox" class="checkbox checkbox-sm" checked={allOn} onchange={toggleAll} aria-label={t('shop.orders.select_all')} /></th>
							<th>{t('shop.orders.col.order')}</th>
							<th>{t('shop.orders.col.purchaser')}</th>
							<th>{t('shop.orders.col.status')}</th>
							<th class="hidden text-right lg:table-cell">{t('shop.orders.col.total')}</th>
							<th class="text-right">{t('shop.orders.col.due')}</th>
							<th class="hidden xl:table-cell">{t('shop.orders.col.placed')}</th>
							<th><span class="sr-only">{t('shop.col.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as o (o.id)}
							<tr class="cursor-pointer hover:bg-paper" onclick={(e) => { if (!(e.target as HTMLElement).closest('a, button, input, label, select')) goto(orderHref(o.id)); }}>
								<td>{#if o.fulfillment_status !== 'cancelled'}<input type="checkbox" class="checkbox checkbox-sm" bind:group={selected} value={o.id} aria-label={o.number} />{/if}</td>
								<td><a class="link font-mono text-xs font-semibold" href={orderHref(o.id)}>{o.number}</a><div class="text-xs text-muted">{t('shop.orders.lines_n', { n: o.line_count })}</div></td>
								<td class="font-semibold">{o.purchaser.name}</td>
								<td><OrderBadges payment={o.payment_status} fulfillment={o.fulfillment_status} production={o.production_status} readyToConfirm={highlight(o)} /></td>
								<td class="hidden text-right font-mono text-sm lg:table-cell">{money(o.total)}</td>
								<td class="text-right font-mono text-sm {Number(o.amount_due) > 0 && o.fulfillment_status !== 'cancelled' ? 'font-semibold text-warn-text' : 'text-muted'}">{o.fulfillment_status === 'cancelled' ? '' : money(o.amount_due)}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap text-muted xl:table-cell">{day(o.created)}</td>
								<td class="text-right"><div class="flex items-center justify-end gap-1"><a class="btn btn-ghost btn-xs btn-square" href={orderHref(o.id)} aria-label={o.number} title={o.number}><ChevronRight size={16} /></a></div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>

<Modal bind:open={paidOpen} title={t('shop.orders.bulk.mark_paid')}>
	<form
		method="POST"
		action="?/markPaid"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				paidOpen = false;
			};
		}}>
		<input type="hidden" name="payload" value={JSON.stringify({ order_ids: selected, method })} />
		<p class="text-sm text-muted">{t('shop.orders.bulk.paid_body', { n: selected.length })}</p>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.pay.method')}</span></span>
			<select class="select select-bordered w-full" bind:value={method}>
				{#each MANUAL_METHODS as m (m)}{#if m !== 'voucher'}<option value={m}>{t(`shop.pay.method.${m}`)}</option>{/if}{/each}
			</select>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (paidOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-success gap-2" type="submit" disabled={busy}><CircleCheck size={16} /> {t('shop.orders.bulk.mark_paid')}</button>
		</div>
	</form>
</Modal>
