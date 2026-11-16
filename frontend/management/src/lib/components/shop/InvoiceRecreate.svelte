<script lang="ts">
	import { RefreshCw } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { browserApi, type InvoiceOutcome } from '#lib/shop-orders.ts';

	/**
	 * Rebuilds the PDF of an invoice from the order as it is now (payments, lines, discounts): drops the cached file and renders it
	 * again. It only renders the button; the outcome is reported through `onoutcome` (null again after a few seconds for a success)
	 * so the parent can show it on its own line below the whole row, with `InvoiceOutcomeNote`, and the buttons never move.
	 */
	let { invoiceId, size = 'sm', onoutcome }: { invoiceId: string; size?: 'sm' | 'xs'; onoutcome?: (outcome: InvoiceOutcome | null) => void } = $props();
	let busy = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	const recreate = async () => {
		if (busy) return;
		busy = true;
		clearTimeout(timer);
		onoutcome?.(null);
		const res = await browserApi('POST', `/shop/invoices/${invoiceId}/recreate-pdf`);
		busy = false;
		if (res.ok) {
			onoutcome?.({ kind: 'done', message: t('shop.invc.recreated') });
			timer = setTimeout(() => onoutcome?.(null), 6000);
		} else onoutcome?.({ kind: 'failed', message: res.status === 503 ? t('shop.invc.recreate_unavailable') : res.message && res.message !== 'network' ? res.message : t('shop.invc.recreate_failed') });
	};
</script>

<button class="btn btn-outline {size === 'xs' ? 'btn-xs gap-1' : 'btn-sm gap-2'}" type="button" disabled={busy} onclick={recreate}><RefreshCw size={size === 'xs' ? 13 : 14} class={busy ? 'animate-spin' : ''} aria-hidden="true" /> {t('shop.invc.recreate')}</button>
