<script lang="ts">
	import { enhance } from '$app/forms';
	import { Banknote, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PaymentFields, { type PaymentDraft } from '#lib/components/shop/PaymentFields.svelte';
	import { dateTime, money, type ChargeDetail } from '#lib/charges.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Payment } from '#lib/shop-orders.ts';

	/** The payments of a sent invoice: the totals, the list, record and delete. Posts to the page's `addPayment` / `deletePayment` actions. */
	let { charge, form }: { charge: ChargeDetail; form: Record<string, unknown> | null | undefined } = $props();

	const blank = (): PaymentDraft => ({ id: '', amount: charge.amount_due, method: 'bank_transfer', reference: '', note: '', voucher_number: '', paid_at: '' });
	let busy = $state(false);
	let addOpen = $state(false);
	let delOpen = $state(false);
	let add = $state<PaymentDraft>(blank());
	let target = $state<Payment | null>(null);

	const op = $derived(form && 'op' in form ? String(form.op) : '');
	const errs = $derived<Record<string, string>>(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const failure = $derived(['addPayment', 'deletePayment'].includes(op) && form && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
</script>

<section class="card card-body card-hl gap-3" aria-labelledby="chg-pay">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 id="chg-pay" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Banknote size={16} aria-hidden="true" /> {t('chg.pay.title')}</h2>
		{#if charge.can_record_payment}
			<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => ((add = blank()), (addOpen = true))}><Banknote size={14} /> {t('chg.pay.record')}</button>
		{/if}
	</div>
	{#if failure}<Alert kind="error" title={t('chg.d.failed')}>{failure}</Alert>{/if}
	<div class="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
		<div class="flex justify-between sm:block"><span class="text-muted">{t('chg.pay.total')}</span> <span class="font-mono font-semibold">{money(charge.total)}</span></div>
		<div class="flex justify-between sm:block"><span class="text-muted">{t('chg.pay.paid')}</span> <span class="font-mono font-semibold">{money(charge.amount_paid)}</span></div>
		<div class="flex justify-between sm:block"><span class="text-muted">{t('chg.pay.due')}</span> <span class="font-mono font-semibold {Number(charge.amount_due) > 0 ? 'text-warn-text' : ''}">{money(charge.amount_due)}</span></div>
	</div>
	{#if charge.payments.length}
		<div class="overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('shop.pay.method')}</th><th class="text-right">{t('shop.pay.amount')}</th><th>{t('shop.pay.reference')}</th><th>{t('shop.pay.paid_at')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
				<tbody>
					{#each charge.payments as p (p.id)}
						<tr>
							<td>{t(`shop.pay.method.${p.method}`)}</td>
							<td class="text-right font-mono text-sm">{money(p.amount)}</td>
							<td class="text-sm">{p.reference || '—'}{#if p.note}<div class="text-xs text-muted">{p.note}</div>{/if}</td>
							<td class="font-mono text-xs whitespace-nowrap text-muted">{p.paid_at ? dateTime(p.paid_at) : '—'}</td>
							<td class="text-right"><div class="flex items-center justify-end gap-1"><button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('common.delete')} onclick={() => ((target = p), (delOpen = true))}><Trash2 size={13} /></button></div></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="text-sm text-muted">{t('chg.pay.none')}</p>
	{/if}
</section>

<Modal bind:open={addOpen} title={t('chg.pay.record')} wide>
	<form method="POST" action="?/addPayment" class="grid gap-3" use:enhance={submit(() => (addOpen = false))}>
		{#if op === 'addPayment' && failure}<Alert kind="error">{failure}</Alert>{/if}
		<PaymentFields bind:v={add} errors={op === 'addPayment' ? errs : {}} noVoucher />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Banknote size={16} /> {t('chg.pay.record')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('shop.pay.delete')}>
	<form method="POST" action="?/deletePayment" class="grid gap-3" use:enhance={submit(() => (delOpen = false))}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('chg.pay.delete_body', { amount: money(target?.amount) })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
