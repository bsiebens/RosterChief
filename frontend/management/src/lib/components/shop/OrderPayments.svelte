<script lang="ts">
	import { enhance } from '$app/forms';
	import { Banknote, Copy, Link2, Pencil, Trash2, Undo2, FileText } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PaymentFields, { type PaymentDraft } from '#lib/components/shop/PaymentFields.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTime, money, rowPaymentBadge, type OrderDetail, type Payment } from '#lib/shop-orders.ts';

	/** The payments card of an order: list, record / edit / delete / refund and the online payment link. Posts to the page's actions. */
	let { order, form }: { order: OrderDetail; form: Record<string, unknown> | null | undefined } = $props();

	const toLocal = (iso: string | null) => {
		if (!iso) return '';
		const d = new Date(iso);
		const p = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
	};
	const blank = (): PaymentDraft => ({ id: '', amount: order.amount_due, method: 'cash', reference: '', note: '', voucher_number: '', paid_at: '' });
	let busy = $state(false);
	let addOpen = $state(false);
	let editOpen = $state(false);
	let delOpen = $state(false);
	let refundOpen = $state(false);
	let linkOpen = $state(false);
	let add = $state<PaymentDraft>(blank());
	let edit = $state<PaymentDraft>(blank());
	let target = $state<Payment | null>(null);
	let linkAmount = $state('');
	let copied = $state('');

	const editable = $derived(order.fulfillment_status !== 'cancelled');
	const op = $derived(form && 'op' in form ? String(form.op) : '');
	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const msg = $derived(form && 'message' in form && form.message ? String(form.message) : '');
	const failure = $derived(['addPayment', 'editPayment', 'deletePayment', 'refundPayment', 'payLink'].includes(op) && form && !('done' in form) ? msg || t('common.error.generic') : '');
	const link = $derived(form && 'link' in form ? String(form.link) : '');

	function openAdd() {
		add = blank();
		addOpen = true;
	}
	function openEdit(p: Payment) {
		target = p;
		edit = { id: p.id, amount: p.amount, method: p.method, reference: p.reference, note: p.note, voucher_number: p.voucher?.number ?? '', paid_at: toLocal(p.paid_at) };
		editOpen = true;
	}
	const manual = (p: Payment) => p.status === 'confirmed' && p.method !== 'online';
	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = text;
			setTimeout(() => (copied = ''), 2000);
		} catch {
			/* the link is selectable in the field */
		}
	}
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
</script>

<section class="card card-body gap-3" aria-labelledby="pay-h">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 id="pay-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Banknote size={16} aria-hidden="true" /> {t('shop.orders.card.payments')}</h2>
		{#if editable}
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={openAdd}><Banknote size={14} /> {t('shop.pay.record')}</button>
				{#if order.online_payments_available && Number(order.amount_due) > 0}
					<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => ((linkAmount = order.amount_due), (linkOpen = true))}><Link2 size={14} /> {t('shop.pay.link_create')}</button>
				{/if}
			</div>
		{/if}
	</div>
	{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}
	{#if link}
		<Alert kind="success" title={t('shop.pay.link_ready')}>
			<div class="flex flex-wrap items-center gap-2">
				<input class="input input-bordered input-sm min-w-0 flex-1 font-mono text-xs" readonly value={link} aria-label={t('shop.pay.link')} onfocus={(e) => e.currentTarget.select()} />
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => copy(link)}><Copy size={14} /> {copied === link ? t('shop.pay.copied') : t('shop.pay.copy')}</button>
			</div>
		</Alert>
	{/if}

	<div class="mb-1 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
		<div class="flex justify-between sm:block"><span class="text-muted">{t('shop.orders.total')}</span> <span class="font-mono font-semibold">{money(order.total)}</span></div>
		<div class="flex justify-between sm:block"><span class="text-muted">{t('shop.orders.paid')}</span> <span class="font-mono font-semibold">{money(order.amount_paid)}</span></div>
		<div class="flex justify-between sm:block"><span class="text-muted">{t('shop.orders.due')}</span> <span class="font-mono font-semibold {Number(order.amount_due) > 0 ? 'text-warn-text' : ''}">{money(order.amount_due)}</span></div>
	</div>

	{#if order.payments.length}
		<div class="overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('shop.pay.method')}</th><th class="text-right">{t('shop.pay.amount')}</th><th>{t('shop.pay.status')}</th><th>{t('shop.pay.reference')}</th><th>{t('shop.pay.paid_at')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
				<tbody>
					{#each order.payments as p (p.id)}
						<tr>
							<td>{t(`shop.pay.method.${p.method}`)}{#if p.voucher}<span class="ml-1 font-mono text-xs text-muted">({p.voucher.number})</span>{/if}</td>
							<td class="text-right font-mono text-sm">{money(p.amount)}</td>
							<td><span class="badge badge-sm {rowPaymentBadge(p.status)}">{t(`shop.pay.status.${p.status}`)}</span></td>
							<td class="text-sm">{p.reference || '—'}{#if p.note}<div class="text-xs text-muted">{p.note}</div>{/if}{#if p.status === 'pending' && p.payment_url}
									<button class="btn btn-ghost btn-xs gap-1" type="button" onclick={() => copy(p.payment_url)}><Copy size={12} /> {copied === p.payment_url ? t('shop.pay.copied') : t('shop.pay.copy_link')}</button>{/if}</td>
							<td class="font-mono text-xs whitespace-nowrap text-muted">{p.paid_at ? dateTime(p.paid_at) : '—'}</td>
							<td class="text-right whitespace-nowrap">
								<div class="flex justify-end gap-1">
									{#if p.status === 'confirmed' || p.status === 'pending'}<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => openEdit(p)}><Pencil size={12} /> {t('common.edit')}</button>{/if}
									{#if p.status === 'confirmed'}<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('shop.pay.refund')} title={t('shop.pay.refund')} onclick={() => ((target = p), (refundOpen = true))}><Undo2 size={12} /></button>{/if}
									{#if manual(p) || p.status === 'failed' || p.status === 'pending'}<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('common.delete')} onclick={() => ((target = p), (delOpen = true))}><Trash2 size={12} /></button>{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="text-sm text-muted">{t('shop.pay.none')}</p>
	{/if}
	{#if Number(order.amount_due) > 0 && order.payment_instructions}
		<div class="rounded-lg border border-line p-3">
			<h3 class="font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><FileText size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.payment_instructions')}</h3>
			<p class="mt-1 text-sm whitespace-pre-line">{order.payment_instructions}</p>
			<p class="mt-1 text-xs text-muted">{t('shop.orders.payment_instructions_note')}</p>
		</div>
	{/if}
</section>

<Modal bind:open={addOpen} title={t('shop.pay.record')} wide>
	<form method="POST" action="?/addPayment" class="grid gap-3" use:enhance={submit(() => (addOpen = false))}>
		{#if op === 'addPayment' && failure}<Alert kind="error">{failure}</Alert>{/if}
		<PaymentFields bind:v={add} errors={op === 'addPayment' ? errs : {}} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Banknote size={16} /> {t('shop.pay.record')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={editOpen} title={t('shop.pay.edit')} wide>
	<form method="POST" action="?/editPayment" class="grid gap-3" use:enhance={submit(() => (editOpen = false))}>
		<input type="hidden" name="id" value={edit.id} />
		<PaymentFields bind:v={edit} errors={op === 'editPayment' ? errs : {}} edit locked={!!target && !manual(target)} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (editOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('shop.pay.delete')}>
	<form method="POST" action="?/deletePayment" class="grid gap-3" use:enhance={submit(() => (delOpen = false))}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.pay.delete_body', { amount: money(target?.amount) })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={refundOpen} title={t('shop.pay.refund')}>
	<form method="POST" action="?/refundPayment" class="grid gap-3" use:enhance={submit(() => (refundOpen = false))}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.pay.refund_body', { amount: money(target?.amount) })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (refundOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Undo2 size={16} /> {t('shop.pay.refund')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={linkOpen} title={t('shop.pay.link_create')}>
	<form method="POST" action="?/payLink" class="grid gap-3" use:enhance={submit(() => (linkOpen = false))}>
		<p class="text-sm text-muted">{t('shop.pay.link_body')}</p>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.pay.amount')}</span></span>
			<input class="input input-bordered w-full" name="amount" type="number" step="0.01" min="0.01" bind:value={linkAmount} />
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (linkOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Link2 size={16} /> {t('shop.pay.link_create')}</button>
		</div>
	</form>
</Modal>
