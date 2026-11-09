<script lang="ts">
	import { goto } from '$app/navigation';
	import { ArrowLeft, History } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTime, MOVEMENT_REASONS, stockLabel } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const href = (patch: Partial<{ product: string; reason: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.product) sp.set('product', f.product);
		if (f.reason) sp.set('reason', f.reason);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.product || !!data.filters.reason);
	usePageHeader(() => ({ title: t('shop.mov.title'), context: t('shop.nav.inventory'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/inventory"><ArrowLeft size={16} /> {t('shop.inv.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.mov.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar showClear={filtered} onclear={() => goto('?', { reset: false })}>
		<select class="select w-auto" aria-label={t('shop.col.product')} value={data.filters.product} onchange={(e) => goto(href({ product: e.currentTarget.value }), { replace: true, reset: false })}>
			<option value="">{t('shop.mov.all_products')}</option>
			{#each data.products as p (p.id)}<option value={p.id}>{p.label}</option>{/each}
		</select>
		<select class="select w-auto" aria-label={t('shop.mov.reason')} value={data.filters.reason} onchange={(e) => goto(href({ reason: e.currentTarget.value }), { replace: true, reset: false })}>
			<option value="">{t('shop.mov.all_reasons')}</option>
			{#each MOVEMENT_REASONS as r (r)}<option value={r}>{t(`shop.mov.reason.${r}`)}</option>{/each}
		</select>
	</ListFilterBar>
	<div class="grid gap-4 px-7 py-5">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted"><History size={28} /><p>{t('shop.mov.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('shop.mov.when')}</th><th>{t('shop.col.product')}</th><th>{t('shop.mov.reason')}</th><th class="text-right">{t('shop.inv.quantity')}</th><th>{t('shop.inv.note')}</th><th>{t('shop.mov.by')}</th></tr></thead>
					<tbody>
						{#each data.rows as m (m.id)}
							<tr>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{dateTime(m.created)}</td>
								<td class="font-semibold">{stockLabel(m)}</td>
								<td><span class="badge badge-sm">{t(`shop.mov.reason.${m.reason}`)}</span>{#if m.order_number}<a class="link ml-2 font-mono text-xs" href="/shop/orders/{m.order_id}">{m.order_number}</a>{/if}</td>
								<td class="text-right font-mono text-sm font-bold {m.quantity < 0 ? 'text-error' : 'text-success'}">{m.quantity > 0 ? '+' : ''}{m.quantity}</td>
								<td class="text-sm text-muted">{m.note}</td>
								<td class="text-sm">{m.created_by ?? t('common.none')}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
