<script lang="ts">
	import { enhance } from '$app/forms';
	import { FileDown, FilePlus, FileText } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import InvoiceOutcomeNote from '#lib/components/shop/InvoiceOutcomeNote.svelte';
	import InvoiceRecreate from '#lib/components/shop/InvoiceRecreate.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { invoicePdfHref, type InvoiceOutcome, type OrderDetail } from '#lib/shop-orders.ts';

	/** The invoice card of an order: the invoice (number, PDF) or a button to create it. Posts to the page's `createInvoice` action. */
	let { order, canInvoice, form }: { order: OrderDetail; canInvoice: boolean; form: Record<string, unknown> | null | undefined } = $props();
	let open = $state(false);
	let busy = $state(false);
	let dueDate = $state('');
	let recreated = $state<InvoiceOutcome | null>(null);
	const failure = $derived(form && 'op' in form && form.op === 'createInvoice' && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	const dueErr = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>)?.due_date ?? '') : '');
</script>

<section class="card card-body gap-3" aria-labelledby="inv-h">
	<h2 id="inv-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><FileText size={16} aria-hidden="true" /> {t('shop.orders.card.invoice')}</h2>
	{#if failure && !open}<Alert kind="error">{failure}</Alert>{/if}
	{#if order.invoice}
		<div class="flex flex-wrap items-center justify-between gap-2">
			<span class="font-mono text-sm font-semibold">{order.invoice.number}</span>
			<div class="flex flex-wrap items-center gap-2">
				<a class="btn btn-outline btn-sm gap-2" href={invoicePdfHref(order.invoice.id)} target="_blank" rel="noopener"><FileDown size={14} /> {t('shop.invc.pdf')}</a>
				{#if canInvoice}<InvoiceRecreate invoiceId={order.invoice.id} onoutcome={(o) => (recreated = o)} />{/if}
				{#if canInvoice}<a class="btn btn-outline btn-sm" href="/shop/invoices?q={order.invoice.number}">{t('shop.invc.open')}</a>{/if}
			</div>
		</div>
		{#if recreated}<InvoiceOutcomeNote outcome={recreated} />{/if}
	{:else if !canInvoice}
		<p class="text-sm text-muted">{t('shop.orders.invoice_none')}</p>
	{:else if order.fulfillment_status === 'cancelled'}
		<p class="text-sm text-muted">{t('shop.orders.invoice_cancelled')}</p>
	{:else}
		<p class="text-sm text-muted">{t('shop.orders.invoice_none')}</p>
		<button class="btn btn-outline gap-2 self-start" type="button" onclick={() => (open = true)}><FilePlus size={16} /> {t('shop.orders.invoice_create')}</button>
	{/if}
</section>

<Modal bind:open title={t('shop.orders.invoice_create')}>
	<form
		method="POST"
		action="?/createInvoice"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'success') open = false;
			};
		}}>
		{#if failure}<Alert kind="error">{failure}</Alert>{/if}
		<p class="text-sm text-muted">{t('shop.orders.invoice_body')}</p>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.invc.due_date')}</span></span>
			<input class="input input-bordered w-full" type="date" name="due_date" bind:value={dueDate} />
			<span class="label-text-alt mt-1 block">{dueErr || t('shop.invc.due_default')}</span>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><FilePlus size={16} /> {t('shop.orders.invoice_create')}</button>
		</div>
	</form>
</Modal>
