<script lang="ts">
	import { enhance } from '$app/forms';
	import { PackageCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { lineLabel, orderHref, personalization, type BatchDetail } from '#lib/shop-orders.ts';

	/** Receive a batch: everything outstanding at once, or the quantities entered per line (partial deliveries allowed). */
	let { batch }: { batch: BatchDetail } = $props();
	let qty = $state<Record<string, string>>({});
	let busy = $state(false);
	const outstanding = (l: { quantity: number; received_quantity: number }) => Math.max(0, l.quantity - l.received_quantity);
	const items = $derived(
		batch.lines
			.map((l) => ({ line_id: l.id, quantity: Math.floor(Number(qty[l.id] ?? 0)) }))
			.filter((i) => i.quantity > 0)
	);
	const tooMany = $derived(batch.lines.some((l) => Number(qty[l.id] ?? 0) > outstanding(l)));
</script>

<form
	method="POST"
	action="?/receive"
	class="grid gap-3"
	use:enhance={() => {
		busy = true;
		return async ({ update, result }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') qty = {};
		};
	}}>
	<input type="hidden" name="id" value={batch.id} />
	<input type="hidden" name="items" value={JSON.stringify(items)} />
	<div class="overflow-x-auto">
		<table class="table table-sm">
			<thead>
				<tr><th>{t('shop.orders.col.order')}</th><th>{t('shop.orders.line.product')}</th><th class="text-right">{t('shop.prod.ordered_qty')}</th><th class="text-right">{t('shop.prod.received')}</th><th class="text-right">{t('shop.prod.receive_now')}</th></tr>
			</thead>
			<tbody>
				{#each batch.lines as l (l.id)}
					{@const left = outstanding(l)}
					<tr class={left === 0 ? 'text-muted' : ''}>
						<td><a class="link font-mono text-xs" href={orderHref(l.order.id)}>{l.order.number}</a><div class="text-xs text-muted">{l.purchaser.name}</div></td>
						<td class="text-sm font-semibold">{lineLabel(l)}{#if personalization(l)}<div class="font-mono text-xs font-normal text-muted">{personalization(l)}</div>{/if}</td>
						<td class="text-right font-mono text-sm">{l.quantity}</td>
						<td class="text-right font-mono text-sm">{l.received_quantity}</td>
						<td class="text-right">
							{#if left > 0}
								<input class="input input-bordered input-sm w-20 text-right font-mono" type="number" min="0" max={left} placeholder={String(left)} bind:value={qty[l.id]} aria-label={t('shop.prod.receive_now_for', { name: lineLabel(l) })} />
							{:else}<span class="badge badge-sm badge-success">{t('shop.prod.done')}</span>{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if tooMany}<p class="text-xs text-error">{t('shop.prod.too_many')}</p>{/if}
	<div class="flex flex-wrap items-center justify-end gap-2">
		<button class="btn btn-outline gap-2" type="submit" name="mode" value="entered" disabled={busy || !items.length || tooMany}>{t('shop.prod.receive_entered')}</button>
		<button class="btn btn-primary gap-2" type="submit" name="mode" value="all" disabled={busy}><PackageCheck size={16} /> {t('shop.prod.receive_all')}</button>
	</div>
</form>
