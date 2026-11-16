<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fulfillmentBadge, paymentBadge, productionBadge, type FulfillmentStatus, type PaymentStatus, type ProductionStatus } from '#lib/shop-orders.ts';

	/** Payment, fulfilment and production status of an order as badges. `readyToConfirm` adds a warning badge. A cancelled order shows one "cancelled" badge. */
	let { payment, fulfillment, production = null, readyToConfirm = false }: { payment: PaymentStatus; fulfillment: FulfillmentStatus; production?: ProductionStatus | null; readyToConfirm?: boolean } = $props();
</script>

<span class="inline-flex flex-wrap items-center gap-1.5">
	{#if fulfillment === 'cancelled'}
		<span class="badge badge-sm badge-error">{t('shop.orders.fulfillment.cancelled')}</span>
	{:else}
		<span class="badge badge-sm {paymentBadge(payment)}">{t(`shop.orders.payment.${payment}`)}</span>
		<span class="badge badge-sm {fulfillmentBadge(fulfillment)}">{t(`shop.orders.fulfillment.${fulfillment}`)}</span>
		{#if readyToConfirm}<span class="badge badge-sm badge-warning">{t('shop.orders.ready_to_confirm')}</span>{/if}
		{#if production}<span class="badge badge-sm {productionBadge(production)}">{t('shop.orders.production_prefix')} {t(`shop.orders.production.${production}`)}</span>{/if}
	{/if}
</span>
