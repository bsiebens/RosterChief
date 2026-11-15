<script lang="ts">
	import { enhance } from '$app/forms';
	import { Activity, NotebookPen, UserRound, ArrowLeft, ArrowRight, Bell, CircleCheck, PackageCheck, RotateCcw, Save, X, ListOrdered } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import OrderAdjust from '#lib/components/shop/OrderAdjust.svelte';
	import OrderBadges from '#lib/components/shop/OrderBadges.svelte';
	import OrderInvoiceCard from '#lib/components/shop/OrderInvoiceCard.svelte';
	import OrderPayments from '#lib/components/shop/OrderPayments.svelte';
	import OrderStatusBadge from '#lib/components/shop/OrderStatusBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { batchBadge, browserApi, dateTime, day, lineLabel, MANUAL_METHODS, money, personalization, type BatchDetail } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const order = $derived(data.order);

	let busy = $state(false);
	let readyOpen = $state(false);
	let paidOpen = $state(false);
	let cancelOpen = $state(false);
	let overrideOpen = $state(false);
	let overrideAck = $state(false);
	// Offered when the order has voucher payments; on by default (the amounts go back on the vouchers).
	let refundVouchers = $state(true);
	let batchOpen = $state(false);
	let batchRef = $state<{ id: string; number: string } | null>(null);
	let batch = $state<BatchDetail | null>(null);
	let batchLoading = $state(false);
	let batchFailed = $state(false);
	const showBatch = async (ref: { id: string; number: string }) => {
		batchRef = ref;
		batch = null;
		batchFailed = false;
		batchLoading = true;
		batchOpen = true;
		const res = await browserApi<BatchDetail>('GET', `/shop/production/batches/${ref.id}`);
		if (batchRef?.id !== ref.id) return;
		batchLoading = false;
		if (res.ok && res.data) batch = res.data;
		else batchFailed = true;
	};
	const batchTab = (s: string) => (s === 'received' ? 'received' : 'ordered');
	const ownLines = $derived(new Set(order.lines.map((l) => l.id)));
	let pickup = $state('');
	let payMethod = $state('cash');
	let payRef = $state('');
	// svelte-ignore state_referenced_locally
	let notes = $state(data.order.notes);
	// svelte-ignore state_referenced_locally
	let notesPickup = $state(data.order.pickup_instructions);
	$effect(() => {
		notes = order.notes;
		notesPickup = order.pickup_instructions;
	});
	const notesDirty = $derived(notes !== order.notes || notesPickup !== order.pickup_instructions);

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const failOp = $derived(form && 'op' in form && !('done' in form) ? String(form.op) : '');
	const failure = $derived(failOp && !['addPayment', 'editPayment', 'deletePayment', 'refundPayment', 'payLink', 'createInvoice', 'applyDiscount', 'removeDiscount', 'applyVoucher'].includes(failOp) ? String(form?.message || (form && 'code' in form && form.code ? t(`shop.orders.err.${form.code}`) : '') || t('common.error.generic')) : '');
	const cancelled = $derived(order.fulfillment_status === 'cancelled');
	const active = $derived(!cancelled);
	const hasMerch = $derived(order.lines.some((l) => l.production_status !== null));

	const run = (close?: () => void) => () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
			close?.(); // a failure shows as an alert on the page
		};
	};

	usePageHeader(() => ({ title: t('shop.orders.title_detail', { number: order.number }), context: order.purchaser.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/orders"><ArrowLeft size={16} /> {t('shop.orders.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.orders.title_detail', { number: order.number }))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !done}<Alert kind="success">{t('shop.orders.created')}</Alert>{/if}
	{#if done}<Alert kind="success">{t(`shop.orders.done.${done}`)}</Alert>{/if}
	{#if failure}<Alert kind="error" title={failOp === 'cancel' ? t('shop.orders.cancel_failed') : t('shop.action_failed')}>{failure}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body gap-3" aria-labelledby="lines-h">
				<h2 id="lines-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} aria-hidden="true" /> {t('shop.orders.card.lines')}</h2>
				<div class="overflow-x-auto">
					<table class="table">
						<thead>
							<tr>
								<th>{t('shop.orders.line.product')}</th>
								<th>{t('shop.orders.line.beneficiary')}</th>
								{#if hasMerch}<th>{t('shop.orders.line.production')}</th>{/if}
								<th class="text-right">{t('shop.orders.line.qty')}</th>
								<th class="text-right">{t('shop.orders.line.unit')}</th>
								<th class="text-right">{t('shop.orders.line.total')}</th>
							</tr>
						</thead>
						<tbody>
							{#each order.lines as l (l.id)}
								<tr>
									<td class="font-semibold">{lineLabel(l)}{#if personalization(l)}<div class="font-mono text-xs font-normal text-muted">{personalization(l)}</div>{/if}</td>
									<td class="text-sm">{l.beneficiary?.name ?? '—'}{#if l.team}<div class="text-xs text-muted">{l.team.name}</div>{/if}</td>
									{#if hasMerch}
										<td class="text-sm">
											{#if l.production_status}
												<OrderStatusBadge kind="production" value={l.production_status} />
												{#if l.production_status !== 'pending'}<span class="ml-1 font-mono text-xs text-muted">{l.received_quantity}/{l.quantity}</span>{/if}
												{#if l.production_batch}{@const pb = l.production_batch}<button class="link ml-1 font-mono text-xs" type="button" aria-label={t('shop.orders.batch.open', { number: pb.number })} onclick={() => showBatch(pb)}>{pb.number}</button>{/if}
											{:else}<span class="text-dim">—</span>{/if}
										</td>
									{/if}
									<td class="text-right font-mono text-sm">{l.quantity}</td>
									<td class="text-right font-mono text-sm">{money(l.unit_price)}</td>
									<td class="text-right font-mono text-sm">{money(l.line_total)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<dl class="ml-auto grid w-full max-w-sm gap-1 text-sm">
					<div class="flex justify-between"><dt class="text-muted">{t('shop.orders.subtotal')}</dt><dd class="font-mono">{money(order.subtotal)}</dd></div>
					{#each order.applied_discounts as d (d.id)}
						<div class="flex justify-between"><dt class="text-muted">{t('shop.orders.discount')}: {d.description}{#if d.discount_type === 'percentage'}<span class="ml-1">({Number(d.discount_amount)}%)</span>{/if}</dt><dd class="font-mono">−{money(d.amount)}</dd></div>
					{/each}
					<div class="flex justify-between border-t border-line pt-1 font-semibold"><dt>{t('shop.orders.total')}</dt><dd class="font-mono">{money(order.total)}</dd></div>
				</dl>
			</section>

			{#if active && (order.can_edit_discounts || order.can_apply_voucher)}<OrderAdjust {order} {form} vouchers={data.vouchers} />{/if}

			<OrderPayments {order} {form} />
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-label={t('shop.orders.card.side')}>
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Activity size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.card.status')}</h2>
				<div class="font-display text-[32px] leading-none font-extrabold text-ink tabular-nums">{money(order.total)}</div>
				<OrderBadges payment={order.payment_status} fulfillment={order.fulfillment_status} production={order.production_status} readyToConfirm={order.ready_to_confirm && !order.is_closed} />
				<div class="text-xs text-muted">{t('shop.orders.placed', { date: dateTime(order.created) })}{#if order.created_by}{' '}· {order.created_by}{/if}</div>
				{#if order.pickup_instructions}<p class="text-sm"><span class="text-muted">{t('shop.orders.pickup_instructions')}:</span> {order.pickup_instructions}</p>{/if}
				{#if active}
					<div class="grid gap-2">
						{#if Number(order.amount_due) > 0}
							<button class="btn btn-success gap-2" type="button" onclick={() => (paidOpen = true)}><CircleCheck size={16} /> {t('shop.orders.act.mark_paid')}</button>
						{/if}
						{#if order.fulfillment_status === 'not_ready'}
							<button class="btn btn-info gap-2" type="button" onclick={() => ((pickup = order.pickup_instructions || order.default_pickup_instructions), (readyOpen = true))}><Bell size={16} /> {t('shop.orders.act.ready')}</button>
						{/if}
							<ActionList>
								{#if order.fulfillment_status !== 'delivered'}
									<form method="POST" action="?/deliver" use:enhance={run()}>
										<ActionRow icon={PackageCheck} type="submit" disabled={busy}>{t('shop.orders.act.deliver')}</ActionRow>
									</form>
								{/if}
								{#if order.fulfillment_status !== 'not_ready'}
									<form method="POST" action="?/reopen" use:enhance={run()}>
										<ActionRow icon={RotateCcw} type="submit" disabled={busy}>{order.fulfillment_status === 'delivered' ? t('shop.orders.act.reopen_delivered') : t('shop.orders.act.reopen')}</ActionRow>
									</form>
								{/if}
								{#if order.fulfillment_status !== 'delivered'}
									{#if order.can_cancel}
										<ActionRow icon={X} danger onclick={() => (cancelOpen = true)}>{t('shop.orders.act.cancel')}</ActionRow>
									{:else if order.can_override_cancel}
										<ActionRow icon={X} danger onclick={() => ((overrideAck = false), (overrideOpen = true))}>{t('shop.orders.act.cancel_anyway')}</ActionRow>
									{/if}
								{/if}
							</ActionList>
					</div>
					{#if !order.can_cancel && order.cancel_blocked_reason}
						<div role="status"><Alert kind="warning" title={t('shop.orders.cancel_blocked')}>{order.cancel_blocked_reason}</Alert></div>
					{/if}
				{/if}
			</section>

			<section class="card card-body gap-2">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><UserRound size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.card.purchaser')}</h2>
				<a class="link text-lg font-semibold" href="/members/{order.purchaser.id}">{order.purchaser.name}</a>
			</section>

			<OrderInvoiceCard {order} canInvoice={data.canInvoice} {form} />

			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><NotebookPen size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.card.notes')}</h2>
				<form method="POST" action="?/saveNotes" class="grid gap-3" use:enhance={run()}>
					<label class="form-control w-full">
						<span class="label"><span class="label-text">{t('shop.orders.notes')}</span></span>
						<textarea class="textarea textarea-bordered w-full" name="notes" rows="3" bind:value={notes}></textarea>
					</label>
					<label class="form-control w-full">
						<span class="label"><span class="label-text">{t('shop.orders.pickup_instructions')}</span></span>
						<textarea class="textarea textarea-bordered w-full" name="pickup_instructions" rows="2" bind:value={notesPickup}></textarea>
					</label>
					<button class="btn btn-outline gap-2 self-start" type="submit" disabled={busy || !notesDirty}><Save size={16} /> {t('common.save')}</button>
				</form>
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={readyOpen} title={t('shop.orders.act.ready')}>
	<form method="POST" action="?/ready" class="grid gap-3" use:enhance={run(() => (readyOpen = false))}>
		<p class="text-sm text-muted">{t('shop.orders.ready_body', { number: order.number })}</p>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.orders.pickup_instructions')}</span></span>
			<textarea class="textarea textarea-bordered w-full" name="pickup_instructions" rows="3" bind:value={pickup}></textarea>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (readyOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-info gap-2" type="submit" disabled={busy}><Bell size={16} /> {t('shop.orders.act.ready')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={paidOpen} title={t('shop.orders.act.mark_paid')}>
	<form method="POST" action="?/markPaid" class="grid gap-3" use:enhance={run(() => (paidOpen = false))}>
		<p class="text-sm text-muted">{t('shop.orders.paid_body', { amount: money(order.amount_due), number: order.number })}</p>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.pay.method')}</span></span>
			<select class="select select-bordered w-full" name="method" bind:value={payMethod}>
				{#each MANUAL_METHODS as m (m)}{#if m !== 'voucher'}<option value={m}>{t(`shop.pay.method.${m}`)}</option>{/if}{/each}
			</select>
		</label>
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.pay.reference')}</span></span>
			<input class="input input-bordered w-full" name="reference" bind:value={payRef} />
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (paidOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-success gap-2" type="submit" disabled={busy}><CircleCheck size={16} /> {t('shop.orders.act.mark_paid')}</button>
		</div>
	</form>
</Modal>

{#snippet refundOption()}
	{#if order.voucher_refund_count > 0}
		<label class="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-3 text-sm">
			<input type="checkbox" class="checkbox checkbox-sm mt-0.5" name="refund_vouchers" value="true" bind:checked={refundVouchers} />
			<span class="grid gap-0.5">
				<span class="font-semibold">{t('shop.orders.cancel_refund_vouchers')}</span>
				<span class="text-xs text-muted">{t('shop.orders.cancel_refund_help', { amount: money(order.voucher_refund_total) })}</span>
			</span>
		</label>
		<p class="text-xs text-muted">{t('shop.orders.cancel_other_payments')}</p>
	{/if}
{/snippet}

<Modal bind:open={cancelOpen} title={t('shop.orders.act.cancel')}>
	<form method="POST" action="?/cancel" class="grid gap-3" use:enhance={run(() => (cancelOpen = false))}>
		<p>{t('shop.orders.cancel_body', { number: order.number })}</p>
		{@render refundOption()}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (cancelOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><X size={16} /> {t('shop.orders.act.cancel')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={overrideOpen} title={t('shop.orders.cancel_anyway_title')}>
	<form method="POST" action="?/cancel" class="grid gap-3" use:enhance={run(() => (overrideOpen = false))}>
		<input type="hidden" name="override" value="true" />
		{#if order.cancel_blocked_reason}<Alert kind="warning" title={t('shop.orders.cancel_blocked')}>{order.cancel_blocked_reason}</Alert>{/if}
		<p class="text-sm">{t('shop.orders.cancel_anyway_body')}</p>
		{@render refundOption()}
		<label class="flex cursor-pointer items-start gap-2 text-sm font-semibold"><input type="checkbox" class="checkbox checkbox-sm mt-0.5" bind:checked={overrideAck} /> {t('shop.orders.cancel_anyway_ack')}</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (overrideOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy || !overrideAck}><X size={16} /> {t('shop.orders.cancel_anyway_submit')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={batchOpen} title={t('shop.prod.batch') + (batchRef ? ` ${batchRef.number}` : '')} wide>
	{#if batchLoading}
		<p class="text-sm text-muted" role="status">{t('shop.orders.batch.loading')}</p>
	{:else if batchFailed || !batch}
		<Alert kind="error">{t('shop.orders.batch.error')}</Alert>
	{:else}
		<div class="grid gap-3">
			<div class="flex flex-wrap items-center gap-2">
				<span class="font-mono font-semibold">{batch.number}</span>
				<span class="badge badge-sm {batchBadge(batch.status)}">{t(`shop.prod.status.${batch.status}`)}</span>
				<span class="font-mono text-xs text-muted">{t('shop.orders.batch.received', { got: batch.received_quantity, qty: batch.quantity })}</span>
			</div>
			<dl class="grid gap-1 text-sm sm:grid-cols-2">
				<div><dt class="text-xs text-muted">{t('shop.prod.supplier')}</dt><dd>{batch.supplier || t('shop.prod.no_supplier')}</dd></div>
				<div><dt class="text-xs text-muted">{t('shop.prod.reference')}</dt><dd>{batch.reference || '—'}</dd></div>
				{#if batch.ordered_at}<div><dt class="text-xs text-muted">{t('shop.prod.status.ordered')}</dt><dd class="font-mono">{day(batch.ordered_at)}</dd></div>{/if}
				{#if batch.note}<div class="sm:col-span-2"><dt class="text-xs text-muted">{t('shop.prod.note')}</dt><dd>{batch.note}</dd></div>{/if}
			</dl>
			<div class="max-h-72 overflow-auto">
				<table class="table table-sm">
					<thead><tr><th>{t('shop.orders.batch.col.order')}</th><th>{t('shop.orders.batch.col.item')}</th><th class="text-right">{t('shop.orders.batch.col.qty')}</th></tr></thead>
					<tbody>
						{#each batch.lines as bl (bl.id)}
							<tr class={(bl.order.id === order.id || ownLines.has(bl.id)) ? 'bg-surface font-semibold' : ''}>
								<td class="font-mono text-xs whitespace-nowrap">{bl.order.number}{#if (bl.order.id === order.id || ownLines.has(bl.id))}<span class="badge badge-sm badge-info ml-1">{t('shop.orders.batch.this_order')}</span>{/if}</td>
								<td class="text-sm">{lineLabel(bl)}{#if personalization(bl)}<div class="font-mono text-xs font-normal text-muted">{personalization(bl)}</div>{/if}</td>
								<td class="text-right font-mono text-sm">{bl.quantity}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
	<div class="modal-action mt-3 flex items-center justify-between gap-2">
		{#if batchRef}<a class="link link-hover inline-flex items-center gap-1 text-sm font-semibold" href="/shop/production?tab={batch ? batchTab(batch.status) : 'ordered'}">{t('shop.orders.batch.open_production')}<ArrowRight size={14} aria-hidden="true" /></a>{:else}<span></span>{/if}
		<button class="btn btn-outline" type="button" onclick={() => (batchOpen = false)}>{t('common.close')}</button>
	</div>
</Modal>
