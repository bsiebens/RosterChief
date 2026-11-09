<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { ArrowLeft, CircleCheck, PackageCheck, TriangleAlert } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTime, MANUAL_METHODS, money, orderHref, paymentBadge, type DeliveryOrder } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; tab: string }>) => {
		const f = { ...data.filters, ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.tab !== 'all') sp.set('tab', f.tab);
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.tab !== 'all');
	const filterTabs = $derived(['all', 'unpaid', 'paid'].map((key) => ({ key, label: t(`shop.delivery.tab.${key}`), count: data.counts[key as keyof typeof data.counts] })));

	const METHODS = MANUAL_METHODS.filter((m) => m !== 'voucher');
	let busy = $state(false);
	let target = $state<DeliveryOrder | null>(null);
	let paidOpen = $state(false);
	let dueOpen = $state(false);
	let method = $state('cash');
	let reference = $state('');

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const doneNumber = $derived(form && 'number' in form ? String(form.number) : '');
	const failure = $derived(form && 'op' in form && !('done' in form) ? String(form.message || (form && 'code' in form && form.code ? t(`shop.orders.err.${form.code}`) : '') || t('common.error.generic')) : '');

	const open = (o: DeliveryOrder, which: 'paid' | 'due') => {
		target = o;
		method = 'cash';
		reference = '';
		if (which === 'paid') paidOpen = true;
		else dueOpen = true;
	};
	const run = (close?: () => void) => () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
			close?.();
		};
	};

	usePageHeader(() => ({ title: t('shop.delivery.title'), context: data.counts.all === 1 ? t('shop.delivery.count_one') : t('shop.delivery.count', { n: data.counts.all }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/orders"><ArrowLeft size={16} /> {t('shop.orders.back_short')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.delivery.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.delivery.search')} searchPlaceholder={t('shop.delivery.search_placeholder')} onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.tab} label={t('shop.delivery.filter_label')} hrefFor={(key) => href({ tab: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		{#if done === 'handOver'}<Alert kind="success">{t('shop.delivery.handed_over', { number: doneNumber })}</Alert>{/if}
		{#if done === 'markPaid'}<Alert kind="success">{t('shop.delivery.paid_done', { number: doneNumber })}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.orders.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<PackageCheck size={28} />
				<p>{filtered ? t('shop.delivery.empty_filtered') : t('shop.delivery.empty')}</p>
				{#if !filtered}<a class="link" href="/shop/orders">{t('shop.delivery.to_orders')}</a>{/if}
			</div>
		{:else}
			<ul class="grid gap-3">
				{#each data.orders as o (o.id)}
					{@const due = Number(o.amount_due) > 0}
					<li class="card card-body gap-3 md:flex-row md:items-center md:justify-between">
						<div class="grid min-w-0 gap-1">
							<div class="flex flex-wrap items-center gap-2">
								<a class="link font-mono text-sm font-semibold" href={orderHref(o.id)}>{o.number}</a>
								<span class="badge badge-sm {paymentBadge(o.payment_status)}">{t(`shop.orders.payment.${o.payment_status}`)}</span>
								<span class="font-mono text-xs text-muted">{t('shop.delivery.ready_since', { date: dateTime(o.ready_since) })}</span>
							</div>
							<div class="text-lg font-semibold">{o.purchaser.name}{#if o.beneficiaries.length}<span class="ml-2 text-sm font-normal text-muted">{t('shop.delivery.for', { names: o.beneficiaries.join(', ') })}</span>{/if}</div>
							<ul class="text-sm">
								{#each o.items as it, i (i)}
									<li>{it.label} <span class="font-mono">x {it.quantity}</span>{#if it.personalization}<span class="ml-2 font-mono text-xs text-muted">{it.personalization}</span>{/if}</li>
								{/each}
							</ul>
							{#if o.pickup_instructions}<p class="truncate text-xs text-muted" title={o.pickup_instructions}>{o.pickup_instructions}</p>{/if}
						</div>
						<div class="flex shrink-0 flex-col gap-2 md:items-end">
							{#if due}<div class="font-display text-2xl leading-none font-extrabold text-warn-text tabular-nums">{t('shop.delivery.due', { amount: money(o.amount_due) })}</div>{/if}
							<div class="flex flex-wrap gap-2">
								{#if due}
									<button class="btn btn-success btn-lg gap-2 max-md:flex-1" type="button" disabled={busy} onclick={() => open(o, 'paid')}><CircleCheck size={18} /> {t('shop.delivery.mark_paid')}</button>
									<button class="btn btn-primary btn-lg gap-2 max-md:flex-1" type="button" disabled={busy} onclick={() => open(o, 'due')}><PackageCheck size={18} /> {t('shop.delivery.hand_over')}</button>
								{:else}
									<form method="POST" action="?/handOver" class="max-md:flex-1" use:enhance={run()}>
										<input type="hidden" name="id" value={o.id} />
										<input type="hidden" name="number" value={o.number} />
										<button class="btn btn-primary btn-lg w-full gap-2" type="submit" disabled={busy}><PackageCheck size={18} /> {t('shop.delivery.hand_over')}</button>
									</form>
								{/if}
							</div>
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
</div>

<Modal bind:open={paidOpen} title={t('shop.delivery.mark_paid')}>
	{#if target}
		<form method="POST" action="?/markPaid" class="grid gap-3" use:enhance={run(() => (paidOpen = false))}>
			<input type="hidden" name="id" value={target.id} />
			<input type="hidden" name="number" value={target.number} />
			<p class="text-sm text-muted">{t('shop.delivery.mark_paid_body', { amount: money(target.amount_due), number: target.number })}</p>
			<label class="form-control w-full">
				<span class="label"><span class="label-text">{t('shop.pay.method')}</span></span>
				<select class="select select-bordered w-full" name="method" bind:value={method}>
					{#each METHODS as m (m)}<option value={m}>{t(`shop.pay.method.${m}`)}</option>{/each}
				</select>
			</label>
			<label class="form-control w-full">
				<span class="label"><span class="label-text">{t('shop.pay.reference')}</span></span>
				<input class="input input-bordered w-full" name="reference" bind:value={reference} />
			</label>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (paidOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-success gap-2" type="submit" disabled={busy}><CircleCheck size={16} /> {t('shop.delivery.mark_paid')}</button>
			</div>
		</form>
	{/if}
</Modal>

<Modal bind:open={dueOpen} title={t('shop.delivery.not_paid_title')}>
	{#if target}
		<div class="grid gap-4">
			<p class="text-sm">{t('shop.delivery.not_paid_body', { number: target.number, amount: money(target.amount_due) })}</p>
			<form method="POST" action="?/handOver" class="grid gap-3" use:enhance={run(() => (dueOpen = false))}>
				<input type="hidden" name="id" value={target.id} />
				<input type="hidden" name="number" value={target.number} />
				<label class="form-control w-full">
					<span class="label"><span class="label-text">{t('shop.pay.method')}</span></span>
					<select class="select select-bordered w-full" name="method" bind:value={method}>
						{#each METHODS as m (m)}<option value={m}>{t(`shop.pay.method.${m}`)}</option>{/each}
					</select>
				</label>
				<label class="form-control w-full">
					<span class="label"><span class="label-text">{t('shop.pay.reference')}</span></span>
					<input class="input input-bordered w-full" name="reference" bind:value={reference} />
				</label>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><CircleCheck size={16} /> {t('shop.delivery.collect')}</button>
			</form>
			<form method="POST" action="?/handOver" class="grid gap-2 border-t border-line pt-3" use:enhance={run(() => (dueOpen = false))}>
				<input type="hidden" name="id" value={target.id} />
				<input type="hidden" name="number" value={target.number} />
				<input type="hidden" name="allow_unpaid" value="true" />
				<p class="flex items-start gap-2 text-sm text-warn-text"><TriangleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" /> {t('shop.delivery.without_warning', { amount: money(target.amount_due) })}</p>
				<button class="btn btn-outline btn-warning gap-2" type="submit" disabled={busy}><PackageCheck size={16} /> {t('shop.delivery.without')}</button>
			</form>
			<div class="flex justify-end"><button class="btn btn-ghost" type="button" onclick={() => (dueOpen = false)}>{t('common.cancel')}</button></div>
		</div>
	{/if}
</Modal>
