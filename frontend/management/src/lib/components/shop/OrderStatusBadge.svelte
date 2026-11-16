<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fulfillmentBadge, paymentBadge, productionBadge, type FulfillmentStatus, type PaymentStatus, type ProductionStatus } from '#lib/shop-orders.ts';

	/** One status as a badge: kind picks which status family `value` belongs to. */
	let { kind, value }: { kind: 'payment' | 'fulfillment' | 'production'; value: PaymentStatus | FulfillmentStatus | ProductionStatus } = $props();
	const cls = $derived(kind === 'payment' ? paymentBadge(value as PaymentStatus) : kind === 'fulfillment' ? fulfillmentBadge(value as FulfillmentStatus) : productionBadge(value as ProductionStatus));
</script>

<span class="badge badge-sm {cls}">{t(`shop.orders.${kind}.${value}`)}</span>
