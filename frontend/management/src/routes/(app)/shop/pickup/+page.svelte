<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { ArrowLeft, Bell, PackageCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, money, orderHref, paymentBadge } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const formId = $props.id();
	const me = $derived(data.me);

	let q = $state('');
	let unpaidOnly = $state(false);
	const filtered = $derived(!!q || unpaidOnly);
	const rows = $derived(
		data.orders.filter((o) => {
			if (unpaidOnly && Number(o.amount_due) <= 0) return false;
			const needle = q.toLowerCase();
			return !needle || o.number.toLowerCase().includes(needle) || o.purchaser.name.toLowerCase().includes(needle);
		})
	);

	// Everything is selected by default; a reload starts over, with the (possibly new) default texts.
	let selected = $state<string[]>([]);
	// svelte-ignore state_referenced_locally
	let pickup = $state(data.pickupInstructions);
	// svelte-ignore state_referenced_locally
	let payment = $state(data.paymentInstructions);
	let saveDefault = $state(false);
	$effect(() => {
		const orders = data.orders;
		const p = data.pickupInstructions;
		const pay = data.paymentInstructions;
		untrack(() => {
			selected = orders.map((o) => o.id);
			pickup = p;
			payment = pay;
			saveDefault = false;
		});
	});

	const visibleIds = $derived(rows.map((o) => o.id));
	const allOn = $derived(visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id)));
	const toggleAll = () => (selected = allOn ? selected.filter((id) => !visibleIds.includes(id)) : [...new Set([...selected, ...visibleIds])]);
	const chosen = $derived(data.orders.filter((o) => selected.includes(o.id)));
	const unpaidCount = $derived(chosen.filter((o) => o.payment_status !== 'paid').length);
	const differs = $derived(pickup.trim() !== data.pickupInstructions.trim());

	let busy = $state(false);
	let confirmOpen = $state(false);
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const doneCount = $derived(form && 'count' in form ? Number(form.count) : 0);
	const skipped = $derived(form && 'skipped' in form ? Number(form.skipped) : 0);
	const failure = $derived(form && 'op' in form && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	const payload = $derived(JSON.stringify({ order_ids: selected, pickup_instructions: pickup.trim(), payment_instructions: payment.trim(), save_as_default: saveDefault && differs }));

	usePageHeader(() => ({ title: t('shop.pickup.title'), context: t('shop.pickup.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/orders"><ArrowLeft size={16} /> {t('shop.orders.back_short')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.pickup.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	{#if data.orders.length}
		<ListFilterBar searchLabel={t('shop.pickup.search')} searchPlaceholder={t('shop.pickup.search_placeholder')} onsearch={(v) => (q = v)} showClear={filtered} onclear={() => ((q = ''), (unpaidOnly = false))} value={q}>
			<label class="flex h-9 cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={unpaidOnly} /> {t('shop.pickup.unpaid_only')}</label>
		</ListFilterBar>
	{/if}

	<div class="grid gap-4 px-7 py-5">
		{#if done === 'confirm'}
			<Alert kind="success">{t('shop.pickup.done', { n: doneCount })}{#if skipped > 0} {t('shop.pickup.skipped', { n: skipped })}{/if}</Alert>
		{/if}
		{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.orders.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<PackageCheck size={28} />
				<p>{t('shop.pickup.empty')}</p>
				<a class="link" href="/shop/orders">{t('shop.pickup.to_orders')}</a>
			</div>
		{:else}
			<div class="grid items-start gap-5 xl:grid-cols-3">
				<section class="card overflow-x-auto xl:col-span-2" aria-label={t('shop.pickup.title')}>
					<div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
						<span class="font-display text-sm font-bold tracking-wide text-muted uppercase">{filtered ? t('shop.pickup.count_filtered', { n: rows.length, total: data.orders.length }) : data.orders.length === 1 ? t('shop.pickup.count_one') : t('shop.pickup.count', { n: data.orders.length })}</span>
					</div>
					<div class="max-h-[70vh] overflow-y-auto">
						<table class="table">
							<thead class="sticky top-0 bg-white">
								<tr>
									<th><input type="checkbox" class="checkbox checkbox-sm" checked={allOn} onchange={toggleAll} aria-label={t('shop.orders.select_all')} /></th>
									<th>{t('shop.orders.col.order')}</th>
									<th>{t('shop.orders.col.purchaser')}</th>
									<th>{t('shop.pickup.col.items')}</th>
									<th>{t('shop.orders.col.status')}</th>
									<th>{t('shop.orders.col.placed')}</th>
								</tr>
							</thead>
							<tbody>
								{#each rows as o (o.id)}
									<tr class="hover:bg-surface">
										<td><input type="checkbox" class="checkbox checkbox-sm" bind:group={selected} value={o.id} aria-label={o.number} /></td>
										<td><a class="link font-mono text-xs font-semibold" href={orderHref(o.id)}>{o.number}</a></td>
										<td class="font-semibold">{o.purchaser.name}{#if o.beneficiaries.length}<div class="text-xs font-normal text-muted">{t('shop.pickup.for', { names: o.beneficiaries.join(', ') })}</div>{/if}</td>
										<td class="font-mono text-sm whitespace-nowrap">{t('shop.pickup.items', { lines: o.line_count, qty: o.quantity })}</td>
										<td>
											<span class="badge badge-sm {paymentBadge(o.payment_status)}">{t(`shop.orders.payment.${o.payment_status}`)}</span>
											{#if Number(o.amount_due) > 0}<div class="font-mono text-xs font-semibold text-warn-text">{money(o.amount_due)}</div>{/if}
										</td>
										<td class="font-mono text-xs whitespace-nowrap text-muted">{day(o.created)}</td>
									</tr>
								{:else}
									<tr><td colspan="6" class="py-6 text-center text-muted">{t('shop.orders.empty_filtered')}</td></tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>

				<aside class="grid content-start gap-5 xl:sticky xl:top-20">
					<section class="card card-body gap-3">
						<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.pickup.card.notify')}</h2>
						<div class="form-control w-full">
							<label class="label" for="{formId}-pickup"><span class="label-text">{t('shop.orders.pickup_instructions')}</span></label>
							<textarea id="{formId}-pickup" class="textarea textarea-bordered w-full" rows="4" maxlength="2000" bind:value={pickup}></textarea>
							{#if !pickup.trim()}<span class="mt-1 block text-xs text-warn-text">{t('shop.pickup.pickup_empty')}</span>{/if}
						</div>
						<label class="flex items-center gap-2 text-sm {differs ? 'cursor-pointer' : 'text-muted'}"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={saveDefault} disabled={!differs} /> {t('shop.pickup.save_default')}</label>
						<div class="form-control w-full">
							<label class="label" for="{formId}-pay"><span class="label-text">{t('shop.orders.payment_instructions')}</span></label>
							<textarea id="{formId}-pay" class="textarea textarea-bordered w-full" rows="4" maxlength="2000" bind:value={payment} aria-describedby="{formId}-pay-help"></textarea>
							<span id="{formId}-pay-help" class="label-text-alt mt-1 block">{t('shop.pickup.payment_help')}{#if me.can_manage_settings}<a class="link ml-1" href="/settings">{t('shop.pickup.club_settings')}</a>{/if}</span>
						</div>
						<p class="text-sm font-semibold text-ink" aria-live="polite">{chosen.length === 1 ? t('shop.pickup.summary_one') : t('shop.pickup.summary', { n: chosen.length })}, {t('shop.pickup.summary_unpaid', { m: unpaidCount })}</p>
						<button class="btn btn-primary gap-2" type="button" disabled={!selected.length || busy} onclick={() => (confirmOpen = true)}><Bell size={16} /> {t('shop.pickup.confirm')}</button>
					</section>
				</aside>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={confirmOpen} title={t('shop.pickup.confirm')}>
	<form
		method="POST"
		action="?/confirm"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				confirmOpen = false;
			};
		}}>
		<input type="hidden" name="payload" value={payload} />
		<p class="text-sm text-muted">{chosen.length === 1 ? t('shop.pickup.confirm_body_one') : t('shop.pickup.confirm_body', { n: chosen.length })}</p>
		{#if !pickup.trim()}<Alert kind="warning">{t('shop.pickup.pickup_empty')}</Alert>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (confirmOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy || !selected.length}><Bell size={16} /> {t('shop.pickup.confirm')}</button>
		</div>
	</form>
</Modal>
