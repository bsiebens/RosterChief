<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { lineLabel, orderHref, personalization, type BatchLine } from '#lib/shop-orders.ts';

	/** The lines of a batch as a compact table (order link, purchaser, product, quantities). */
	let { lines, showReceived = false }: { lines: BatchLine[]; showReceived?: boolean } = $props();
</script>

<div class="overflow-x-auto">
	<table class="table table-sm">
		<thead>
			<tr><th>{t('shop.orders.col.order')}</th><th>{t('shop.orders.col.purchaser')}</th><th>{t('shop.orders.line.product')}</th><th class="text-right">{t('shop.orders.line.qty')}</th>{#if showReceived}<th class="text-right">{t('shop.prod.received')}</th>{/if}</tr>
		</thead>
		<tbody>
			{#each lines as l (l.id)}
				<tr>
					<td><a class="link font-mono text-xs" href={orderHref(l.order.id)}>{l.order.number}</a></td>
					<td class="text-sm">{l.purchaser.name}{#if l.beneficiary && l.beneficiary.id !== l.purchaser.id}<div class="text-xs text-muted">{t('shop.prod.for', { name: l.beneficiary.name })}</div>{/if}</td>
					<td class="text-sm font-semibold">{lineLabel(l)}{#if personalization(l)}<div class="font-mono text-xs font-normal text-muted">{personalization(l)}</div>{/if}</td>
					<td class="text-right font-mono text-sm">{l.quantity}</td>
					{#if showReceived}<td class="text-right font-mono text-sm">{l.received_quantity}</td>{/if}
				</tr>
			{/each}
		</tbody>
	</table>
</div>
