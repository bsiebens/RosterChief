<script lang="ts">
	import { enhance } from '$app/forms';
	import { Search, Tag, Ticket, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { browserApi, day, money, type AvailableVoucher, type OrderDetail } from '#lib/shop-orders.ts';
	import type { Voucher } from '#lib/shop.ts';

	/** Discount code and voucher payment for an open order (`can_edit_discounts` / `can_apply_voucher`). Results show as the page's own alerts. */
	let { order, form, vouchers = [] }: { order: OrderDetail; form: unknown; vouchers?: AvailableVoucher[] } = $props();

	let busy = $state(false);
	let code = $state('');
	let number = $state('');
	let amount = $state('');
	let found = $state<Voucher | null>(null);
	let lookupError = $state('');
	let looking = $state(false);

	const f = $derived((form ?? {}) as Record<string, unknown>);
	const op = $derived('op' in f && !('done' in f) ? String(f.op) : '');
	const errs = $derived(op && 'fields' in f ? ((f.fields as Record<string, string>) ?? {}) : {});
	const failure = $derived(op ? String(f.message || (f.code ? t(`shop.orders.err.${f.code}`) : '') || t('common.error.generic')) : '');
	const mine = (...ops: string[]) => ops.includes(op);

	const due = $derived(Number(order.amount_due));
	const defaultAmount = (v: Voucher) => Math.max(0, Math.min(Number(v.available_amount), due)).toFixed(2);
	const amountOk = $derived(Number(amount) > 0 && Number(amount) <= due + 1e-9);
	const canApply = $derived(!!found && found.status === 'usable' && found.number === number.trim() && amountOk && !busy);

	const lookup = async () => {
		const n = number.trim();
		if (!n || looking) return;
		looking = true;
		lookupError = '';
		found = null;
		const res = await browserApi<Voucher>('GET', `/shop/vouchers/lookup?number=${encodeURIComponent(n)}`);
		looking = false;
		if (res.ok && res.data) {
			found = res.data;
			amount = defaultAmount(res.data);
		} else lookupError = res.status === 404 ? t('shop.orders.adj.not_found') : t('shop.orders.adj.lookup_failed');
	};
	const takes = (v: AvailableVoucher) => Math.max(0, Math.min(Number(v.available_amount), due)).toFixed(2);
	const badge = (s: string) => ({ usable: 'badge-success', inactive: 'badge-warning', expired: 'badge-warning', exhausted: 'badge-warning' })[s] ?? 'badge-neutral';

	const run = (reset?: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success' && (result as { data?: { done?: string } }).data?.done) reset?.();
		};
	};
	const clearCode = () => (code = '');
	const clearVoucher = () => {
		number = '';
		amount = '';
		found = null;
	};
</script>

<section class="card card-body gap-4" aria-labelledby="adjust-h">
	<h2 id="adjust-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Tag size={16} aria-hidden="true" /> {t('shop.orders.card.adjust')}</h2>

	<!-- Discount code and voucher side by side (stacked on narrow screens). -->
	<div class="grid gap-x-8 gap-y-5 lg:grid-cols-2">
		<div class="grid content-start gap-3">
			<!-- Same heading style and place as the voucher column, so both stay aligned whatever the state. -->
			<h3 class="font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Tag size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.adj.code')}</h3>
		{#if order.applied_discounts.length}
			<ul class="grid gap-2">
				{#each order.applied_discounts as d (d.id)}
					<li class="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
						<span class="font-semibold">{d.description}<span class="ml-2 font-mono font-normal text-muted">−{money(d.amount)}</span></span>
						{#if order.can_edit_discounts}
							<form method="POST" action="?/removeDiscount" use:enhance={run()}>
								<input type="hidden" name="id" value={d.id} />
								<button class="btn btn-ghost btn-sm btn-square" type="submit" disabled={busy} aria-label={t('shop.orders.adj.remove', { name: d.description })}><X size={16} /></button>
							</form>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{#if mine('removeDiscount')}<Alert kind="error">{failure}</Alert>{/if}

		{#if order.can_edit_discounts && !order.applied_discounts.length}
			<form method="POST" action="?/applyDiscount" class="grid gap-1" use:enhance={run(clearCode)}>
				<label class="sr-only" for="adj-code">{t('shop.orders.adj.code')}</label>
				<div class="flex gap-2">
					<input id="adj-code" class="input input-bordered h-9 min-h-9 w-full font-mono" name="code" autocomplete="off" bind:value={code} aria-invalid={!!(mine('applyDiscount') && (errs.discount_code || errs.code))} />
					<button class="btn btn-outline h-9 min-h-9 gap-2" type="submit" disabled={busy || !code.trim()}><Tag size={16} /> {t('shop.orders.adj.apply')}</button>
				</div>
				{#if mine('applyDiscount') && (errs.discount_code || errs.code)}<p class="text-xs text-error" role="alert">{errs.discount_code || errs.code}</p>{/if}
			</form>
			{#if mine('applyDiscount') && !(errs.discount_code || errs.code)}<Alert kind="error">{failure}</Alert>{/if}
		{:else if !order.can_edit_discounts}
			<p class="text-sm text-muted">{t('shop.orders.adj.locked')}</p>
		{/if}
		</div>
		{#if order.can_apply_voucher}
			<div class="grid content-start gap-3 border-t border-line pt-5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-8">
				<h3 class="font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Ticket size={16} class="shrink-0" aria-hidden="true" /> {t('shop.orders.adj.vlist')}</h3>
				{#if vouchers.length}
					<ul class="grid gap-2">
						{#each vouchers as v (v.id)}
							<li class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-lg border border-line px-3 py-2 text-sm">
								<div class="min-w-0">
									<div class="flex flex-wrap items-center gap-2"><span class="font-mono font-semibold">{v.number}</span><span class="badge badge-sm {v.scope === 'own' ? 'badge-success' : v.scope === 'beneficiary' ? 'badge-warning' : v.scope === 'family' ? 'badge-info' : 'badge-neutral'}">{t(`shop.orders.adj.scope.${v.scope}`)}</span>{#if (v.scope === 'family' || v.scope === 'beneficiary') && v.issued_to}<span class="text-muted">{v.issued_to.name}</span>{/if}</div>
									<div class="text-xs text-muted">{t('shop.orders.adj.available')}: <span class="font-mono">{money(v.available_amount)}</span><span class="ml-3">{t('shop.orders.adj.expires')}: <span class="font-mono">{day(v.expiry_date)}</span></span></div>
								</div>
								<form method="POST" action="?/applyVoucher" use:enhance={run(clearVoucher)}>
									<input type="hidden" name="number" value={v.number} />
									<input type="hidden" name="amount" value={takes(v)} />
									<button class="btn btn-primary btn-sm gap-2" type="submit" disabled={busy || Number(takes(v)) <= 0}><Ticket size={14} /> {t('shop.orders.adj.vapply', { amount: money(takes(v)) })}</button>
								</form>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="text-sm text-muted">{t('shop.orders.adj.vnone', { name: order.purchaser.name })}</p>
				{/if}
				{#if mine('applyVoucher') && !found}<Alert kind="error">{failure}</Alert>{/if}
				<details class="rounded-lg border border-line px-3 py-2">
					<summary class="cursor-pointer text-sm font-semibold">{t('shop.orders.adj.other')}</summary>
					<div class="mt-3 grid">
						<form method="POST" action="?/applyVoucher" class="grid content-start gap-2" use:enhance={run(clearVoucher)}>
							<input type="hidden" name="number" value={number.trim()} />
							<input type="hidden" name="amount" value={amount} />
							<label class="form-control w-full" for="adj-vnum"><span class="label"><span class="label-text">{t('shop.orders.adj.vnumber')}</span></span></label>
							<div class="flex gap-2">
								<input
									id="adj-vnum"
									class="input input-bordered h-9 min-h-9 w-full font-mono"
									autocomplete="off"
									bind:value={number}
									oninput={() => ((found = null), (lookupError = ''))}
									onkeydown={(e) => {
										if (e.key === 'Enter') {
											e.preventDefault();
											lookup();
										}
									}}
									aria-invalid={!!(mine('applyVoucher') && errs.voucher_number)} />
								<button class="btn btn-outline h-9 min-h-9 gap-2" type="button" disabled={looking || !number.trim()} onclick={lookup}><Search size={16} /> {t('shop.orders.adj.lookup')}</button>
							</div>
							{#if mine('applyVoucher') && errs.voucher_number}<p class="text-xs text-error" role="alert">{errs.voucher_number}</p>{/if}
							{#if lookupError}<p class="text-xs text-error" role="alert">{lookupError}</p>{/if}
							{#if found}
								<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" role="status">
									<span class="font-mono font-semibold">{found.number}</span>
									<span class="badge badge-sm {badge(found.status)}">{t(`shop.status.${found.status}`)}</span>
									<span class="text-muted">{t('shop.orders.adj.available')}: <span class="font-mono">{money(found.available_amount)}</span></span>
									<span class="text-muted">{t('shop.orders.adj.expires')}: <span class="font-mono">{day(found.expiry_date)}</span></span>
								</div>
								{#if found.status !== 'usable'}<p class="text-xs text-muted">{t('shop.orders.adj.vunusable')}</p>{/if}
							{/if}
							{#if found && found.status === 'usable'}
								<label class="form-control w-full" for="adj-vamt"><span class="label"><span class="label-text">{t('shop.orders.adj.vamount')}</span><span class="label-text-alt font-mono text-muted">{t('shop.orders.adj.due', { amount: money(order.amount_due) })}</span></span></label>
								<input id="adj-vamt" class="input input-bordered h-9 min-h-9 w-40 font-mono" inputmode="decimal" autocomplete="off" bind:value={amount} aria-invalid={!amountOk || !!(mine('applyVoucher') && errs.amount)} />
								{#if !amountOk}<p class="text-xs text-error">{t('shop.orders.adj.err_amount', { max: money(order.amount_due) })}</p>{/if}
								{#if mine('applyVoucher') && errs.amount}<p class="text-xs text-error" role="alert">{errs.amount}</p>{/if}
							{/if}
							<button class="btn btn-primary h-9 min-h-9 gap-2 self-start" type="submit" disabled={!canApply}><Ticket size={16} /> {amountOk && found ? t('shop.orders.adj.vapply', { amount: money(amount) }) : t('shop.orders.adj.vapply_plain')}</button>
							{#if mine('applyVoucher') && !errs.voucher_number && !errs.amount}<Alert kind="error">{failure}</Alert>{/if}
						</form>

					</div>
				</details>
			</div>
		{/if}
	</div>
</section>
