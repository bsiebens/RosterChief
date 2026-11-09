<script module lang="ts">
	export type LineDraft = { key: number; product_id: string; variant_id: string; quantity: number; beneficiary_id: string; personalization_number: string; personalization_name: string };
	let seq = 0;
	export const newLine = (): LineDraft => ({ key: ++seq, product_id: '', variant_id: '', quantity: 1, beneficiary_id: '', personalization_number: '', personalization_name: '' });
</script>

<script lang="ts">
	import { Plus, Trash2, TriangleAlert } from '@lucide/svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import SearchSelect from '#lib/components/SearchSelect.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { money, type Preview } from '#lib/shop-orders.ts';
	import type { ProductDetail } from '#lib/shop.ts';

	/** The product lines of a new order: product and variant pickers, quantity, optional beneficiary and personalisation, live line totals. */
	let { products, lines = $bindable(), preview = null, estimate, errors = {} }: { products: ProductDetail[]; lines: LineDraft[]; preview?: (Preview['lines'][number] | null)[] | null; estimate: boolean; errors?: Record<string, string> } = $props();

	const items = $derived(products.map((p) => ({ id: p.id, label: p.name, sub: money(p.price) })));
	const productOf = (id: string) => products.find((p) => p.id === id);
	const variantsOf = (id: string) => (productOf(id)?.variants ?? []).filter((v) => v.is_active);
	/** The price shown while the server has not priced the line yet. */
	function unitPrice(l: LineDraft): number {
		const p = productOf(l.product_id);
		if (!p) return 0;
		const v = p.variants.find((x) => x.id === l.variant_id);
		return Number(v ? v.effective_price : p.price) || 0;
	}
	/** Stock warning from the catalogue data: more than is on hand. */
	function warning(l: LineDraft): string {
		const p = productOf(l.product_id);
		if (!p?.inventory_managed) return '';
		const v = p.variants.find((x) => x.id === l.variant_id);
		if (v && v.stock !== null && l.quantity > v.stock) return t('shop.orders.new.stock_warn', { n: v.stock });
		if (!v && !p.variants.length && !p.in_stock) return t('shop.orders.new.out_of_stock');
		return '';
	}
	function pickProduct(l: LineDraft, id: string) {
		l.product_id = id;
		const vs = variantsOf(id);
		l.variant_id = vs.length === 1 ? vs[0].id : '';
	}
</script>

<div class="grid gap-3">
	{#each lines as l, i (l.key)}
		{@const p = productOf(l.product_id)}
		{@const vs = variantsOf(l.product_id)}
		{@const w = warning(l)}
		<div class="grid gap-3 rounded-lg border border-line p-3">
			<div class="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1.2fr)_6rem_8rem_auto] md:items-end">
				<div class="grid gap-1">
					<span class="text-xs font-semibold text-muted">{t('shop.orders.line.product')}</span>
					<SearchSelect {items} bind:value={() => l.product_id, (v) => pickProduct(l, v)} id="line-{l.key}" placeholder={t('shop.orders.new.pick_product')} error={errors[`lines.${i}`]} label={t('shop.orders.line.product')} />
				</div>
				<div class="grid gap-1">
					<span class="text-xs font-semibold text-muted">{t('shop.orders.line.variant')}</span>
					<select class="select select-bordered w-full" bind:value={l.variant_id} disabled={!vs.length} aria-label={t('shop.orders.line.variant')}>
						{#if !vs.length}<option value="">—</option>{:else}<option value="">{t('shop.orders.new.pick_variant')}</option>{/if}
						{#each vs as v (v.id)}<option value={v.id}>{v.name} · {money(v.effective_price)}{v.stock !== null ? ` (${v.stock})` : ''}</option>{/each}
					</select>
				</div>
				<label class="grid gap-1">
					<span class="text-xs font-semibold text-muted">{t('shop.orders.line.qty')}</span>
					<input class="input input-bordered w-full" type="number" min="1" max="999" bind:value={l.quantity} />
				</label>
				<div class="grid gap-1 text-right">
					<span class="text-xs font-semibold text-muted">{t('shop.orders.line.total')}{#if estimate}<span title={t('shop.orders.new.estimate_hint')}> ~</span>{/if}</span>
					<span class="flex h-10 items-center justify-end font-mono text-sm font-semibold">{p ? money(preview?.[i]?.line_total ?? unitPrice(l) * (l.quantity || 0)) : '—'}</span>
				</div>
				<button class="btn btn-outline btn-error btn-square" type="button" aria-label={t('shop.orders.new.remove_line')} disabled={lines.length === 1} onclick={() => (lines = lines.filter((x) => x.key !== l.key))}><Trash2 size={16} /></button>
			</div>
			{#if errors[`lines.${i}`]}<p class="text-xs text-error">{errors[`lines.${i}`]}</p>{/if}
			{#if w}<p class="flex items-center gap-1.5 text-xs font-semibold text-warn-text"><TriangleAlert size={14} aria-hidden="true" /> {w}</p>{/if}
			{#if p}
				<div class="grid gap-3 md:grid-cols-2">
					<EntityPicker kind="member" name="beneficiary-{l.key}" label={t('shop.orders.line.beneficiary_optional')} bind:value={l.beneficiary_id} />
					{#if p.personalization_enabled}
						<div class="grid grid-cols-2 gap-3">
							<label class="form-control w-full"><span class="label"><span class="label-text">{t('shop.orders.line.pers_number')}</span></span><input class="input input-bordered w-full" bind:value={l.personalization_number} maxlength="10" /></label>
							<label class="form-control w-full"><span class="label"><span class="label-text">{t('shop.orders.line.pers_name')}</span></span><input class="input input-bordered w-full" bind:value={l.personalization_name} maxlength="40" /></label>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/each}
	<button class="btn btn-outline gap-2 self-start" type="button" onclick={() => (lines = [...lines, newLine()])}><Plus size={16} /> {t('shop.orders.new.add_line')}</button>
</div>
