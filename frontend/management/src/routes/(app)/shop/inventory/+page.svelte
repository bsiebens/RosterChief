<script lang="ts">
		import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { Boxes, ClipboardList, History, PackagePlus, SlidersHorizontal } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { stockLabel, type StockRow } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; low: boolean; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.low) sp.set('low', '1');
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.low);
	const filterTabs = $derived([
		{ key: 'all', label: t('shop.filter.all') },
		{ key: 'low', label: t('shop.inv.low') }
	]);

	let open = $state(false);
	let busy = $state(false);
	let row = $state<StockRow | null>(null);
	let counted = $state('');
	let note = $state('');
	function ask(r: StockRow) {
		row = r;
		counted = String(r.on_hand);
		note = '';
		open = true;
	}
	const delta = $derived(row && counted !== '' ? Number(counted) - row.on_hand : 0);
	const errs = $derived(form && form.op === 'adjust' && 'fields' in form ? (form.fields as Record<string, string>) : {});
	const done = $derived(form && 'done' in form ? String(form.done) : '');

	usePageHeader(() => ({ title: t('shop.inv.title'), context: data.count === 1 ? t('shop.inv.count_one') : t('shop.inv.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/inventory/count"><ClipboardList size={16} /> {t('shop.count.button')}</a>
	<a class="btn btn-outline gap-2" href="/shop/inventory/movements"><History size={16} /> {t('shop.inv.movements')}</a>
	<a class="btn btn-primary gap-2" href="/shop/inventory/receive"><PackagePlus size={16} /> {t('shop.inv.receive')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.inv.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.product.search')} searchPlaceholder={t('shop.product.search_placeholder')} onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.low ? 'low' : 'all'} label={t('shop.filter.label')} hrefFor={(key) => href({ low: key === 'low' })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if data.received}<Alert kind="success">{t('shop.inv.received', { n: data.received })}</Alert>{/if}
		{#if done === 'adjust'}<Alert kind="success">{form && 'unchanged' in form && form.unchanged ? t('shop.inv.adjust_unchanged') : t('shop.inv.adjusted')}</Alert>{/if}
		{#if form && form.op === 'adjust' && !('done' in form) && !Object.keys(errs).length}<Alert kind="error" title={t('shop.action_failed')}>{form.message || t('common.error.generic')}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Boxes size={28} />
				<p>{filtered ? t('shop.inv.empty_filtered') : t('shop.inv.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('shop.col.product')}</th><th class="text-right">{t('shop.inv.on_hand')}</th><th class="hidden text-right md:table-cell">{t('shop.inv.committed')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
					<tbody>
						{#each data.rows as r (r.product_id + (r.variant_id ?? ''))}
							<tr class="hover:bg-paper {r.on_hand <= 0 ? 'bg-error/5' : ''}">
								<td>
									<a class="link font-semibold" href="/shop/products/{r.product_id}">{r.product_name}</a>{#if r.variant_name}<span class="ml-1.5 text-muted">- {r.variant_name}</span>{/if}
									{#if r.on_hand <= 0}<span class="badge badge-sm badge-error ml-2">{t('shop.out_of_stock')}</span>{/if}
								</td>
								<td class="text-right font-mono text-sm font-bold {r.on_hand <= 0 ? 'text-error' : ''}">{r.on_hand}</td>
								<td class="hidden text-right font-mono text-sm text-muted md:table-cell">{r.committed}</td>
								<td class="text-right"><div class="flex items-center justify-end gap-1"><button class="btn btn-outline btn-xs gap-1" type="button" aria-label={t('shop.inv.adjust_named', { name: stockLabel(r) })} onclick={() => ask(r)}><SlidersHorizontal size={13} /> <span class="sr-only md:not-sr-only">{t('shop.inv.adjust')}</span></button></div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="text-xs text-muted">{t('shop.inv.committed_help')}</p>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>

<Modal bind:open title={t('shop.inv.adjust_title')}>
	<form method="POST" action="?/adjust" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type === 'success') open = false; }; }}>
		<input type="hidden" name="payload" value={JSON.stringify({ product_id: row?.product_id, variant_id: row?.variant_id ?? null, counted, note })} />
		<p class="font-semibold">{row ? stockLabel(row) : ''}</p>
		<p class="text-sm text-muted">{t('shop.inv.adjust_help', { n: row?.on_hand ?? 0 })}</p>
		<div class="grid gap-3 md:grid-cols-2">
			<Field label={t('shop.inv.counted')} type="number" min="0" step="1" bind:value={counted} error={errs.counted} required />
			<Field label={t('shop.inv.note')} bind:value={note} error={errs.note} maxlength={255} />
		</div>
		{#if row && counted !== ''}<p class="text-sm font-mono {delta < 0 ? 'text-error' : ''}">{t('shop.inv.difference')}: {delta > 0 ? '+' : ''}{delta}</p>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy || counted === '' || Number(counted) < 0}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
