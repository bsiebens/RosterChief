<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { Download, Factory, Pencil, Send, Trash2, Truck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import ProductionBatchLines from '#lib/components/shop/ProductionBatchLines.svelte';
	import ProductionReceive from '#lib/components/shop/ProductionReceive.svelte';
	import ProductionSteps from '#lib/components/shop/ProductionSteps.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { batchBadge, batchCsvHref, dateTime, lineLabel, orderHref, personalization, type BatchDetail } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();

	const ordered = $derived(data.open.filter((b) => b.status === 'draft' || b.status === 'ordered'));
	const receiving = $derived(data.open.filter((b) => b.status === 'ordered' || b.status === 'partially_received'));
	const counts = $derived({ to_order: data.lineCount, ordered: ordered.length, received: receiving.length });

	let selected = $state<string[]>([]);
	$effect(() => {
		data.lines;
		selected = [];
	});
	const allOn = $derived(data.lines.length > 0 && selected.length === data.lines.length);
	const toggleAll = () => (selected = allOn ? [] : data.lines.map((l) => l.id));

	let busy = $state(false);
	let createOpen = $state(false);
	let editOpen = $state(false);
	let delOpen = $state(false);
	let target = $state<BatchDetail | null>(null);
	let draft = $state({ reference: '', supplier: '', note: '' });

	const created = $derived(page.url.searchParams.get('created') === '1');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const failure = $derived(form && 'op' in form && !('done' in form) ? String(form.message || (form.code ? t(`shop.prod.err.${form.code}`) : '') || t('common.error.generic')) : '');
	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});

	function openCreate() {
		draft = { reference: '', supplier: '', note: '' };
		createOpen = true;
	}
	function openEdit(b: BatchDetail) {
		target = b;
		draft = { reference: b.reference, supplier: b.supplier, note: b.note };
		editOpen = true;
	}
	const closer = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type !== 'failure') close();
		};
	};

	usePageHeader(() => ({ title: t('shop.prod.title'), context: t('shop.prod.context') }));
</script>

<svelte:head><title>{pageTitle(t('shop.prod.title'))}</title></svelte:head>

<div class="grid gap-4">
	<p class="text-sm text-muted">{t('shop.prod.intro')}</p>
	<ProductionSteps active={data.tab} {counts} />

	{#if created}<Alert kind="success">{t('shop.prod.created')}</Alert>{/if}
	{#if done}<Alert kind="success">{t(`shop.prod.done.${done}`)}</Alert>{/if}
	{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}
	{#if data.error}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}

	{#if data.tab === 'to_order'}
		<section class="card card-body gap-3" aria-labelledby="p1">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<div>
					<h2 id="p1" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Factory size={16} aria-hidden="true" /> {t('shop.prod.step.to_order')}</h2>
					<p class="text-xs text-muted">{t('shop.prod.to_order_help')}</p>
				</div>
				<button class="btn btn-primary gap-2" type="button" disabled={!selected.length} onclick={openCreate}><Send size={16} /> {t('shop.prod.create_batch', { n: selected.length })}</button>
			</div>
			{#if !data.lines.length}
				<p class="py-6 text-center text-sm text-muted">{t('shop.prod.to_order_empty')}</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="table">
						<thead>
							<tr>
								<th><input type="checkbox" class="checkbox checkbox-sm" checked={allOn} onchange={toggleAll} aria-label={t('shop.orders.select_all')} /></th>
								<th>{t('shop.orders.col.order')}</th><th>{t('shop.orders.col.purchaser')}</th><th>{t('shop.orders.line.product')}</th><th class="text-right">{t('shop.orders.line.qty')}</th><th>{t('shop.orders.col.placed')}</th>
							</tr>
						</thead>
						<tbody>
							{#each data.lines as l (l.id)}
								<tr class="hover:bg-surface">
									<td><input type="checkbox" class="checkbox checkbox-sm" bind:group={selected} value={l.id} aria-label={lineLabel(l)} /></td>
									<td><a class="link font-mono text-xs" href={orderHref(l.order.id)}>{l.order.number}</a></td>
									<td class="text-sm">{l.purchaser.name}{#if l.beneficiary && l.beneficiary.id !== l.purchaser.id}<div class="text-xs text-muted">{t('shop.prod.for', { name: l.beneficiary.name })}</div>{/if}</td>
									<td class="text-sm font-semibold">{lineLabel(l)}{#if personalization(l)}<div class="font-mono text-xs font-normal text-muted">{personalization(l)}</div>{/if}</td>
									<td class="text-right font-mono text-sm">{l.quantity}</td>
									<td class="font-mono text-xs whitespace-nowrap text-muted">{dateTime(l.order_created)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{:else if data.tab === 'ordered'}
		{#if !ordered.length}
			<div class="card card-body items-center py-10 text-center text-muted"><Truck size={28} /><p>{t('shop.prod.ordered_empty')}</p></div>
		{/if}
		<!-- Two columns from two batches on; a lone batch takes the full width instead of leaving half the page empty. -->
		<div class="grid gap-4 {ordered.length > 1 ? 'xl:grid-cols-2' : ''}">
			{#each ordered as b (b.id)}
				<section class="card card-body gap-3">
					<div class="flex flex-wrap items-start justify-between gap-2">
						<div class="min-w-0">
							<h2 class="font-mono text-sm font-bold text-ink">{b.number}</h2>
							<p class="text-sm text-muted">{[b.supplier, b.reference].filter(Boolean).join(' · ') || t('shop.prod.no_supplier')}</p>
						</div>
						<span class="badge {batchBadge(b.status)}">{t(`shop.prod.status.${b.status}`)}</span>
					</div>
					{#if b.note}<p class="text-sm">{b.note}</p>{/if}
					<p class="font-mono text-xs text-muted">{t('shop.prod.batch_counts', { lines: b.line_count, qty: b.quantity })}{#if b.ordered_at}{' '}· {t('shop.prod.ordered_on', { date: dateTime(b.ordered_at) })}{/if}</p>
					<details class="rounded-lg border border-line px-3 py-2">
						<summary class="cursor-pointer text-sm font-semibold">{t('shop.prod.show_lines')}</summary>
						<div class="mt-2"><ProductionBatchLines lines={b.lines} /></div>
					</details>
					<div class="flex flex-wrap gap-2">
						<a class="btn btn-outline btn-sm gap-2" href={batchCsvHref(b.id)} download><Download size={14} /> {t('shop.prod.csv')}</a>
						{#if b.status === 'draft'}
							<form method="POST" action="?/markOrdered" use:enhance={closer(() => {})}>
								<input type="hidden" name="id" value={b.id} />
								<button class="btn btn-primary btn-sm gap-2" type="submit" disabled={busy}><Send size={14} /> {t('shop.prod.mark_ordered')}</button>
							</form>
							<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => openEdit(b)}><Pencil size={14} /> {t('common.edit')}</button>
							<button class="btn btn-outline btn-error btn-sm gap-2" type="button" onclick={() => ((target = b), (delOpen = true))}><Trash2 size={14} /> {t('common.delete')}</button>
						{:else}
							<a class="btn btn-outline btn-sm gap-2" href="?tab=received"><Truck size={14} /> {t('shop.prod.go_receive')}</a>
						{/if}
					</div>
					{#if b.status === 'draft'}<p class="text-xs text-muted">{t('shop.prod.draft_hint')}</p>{/if}
				</section>
			{/each}
		</div>
	{:else}
		<Alert kind="info">{t('shop.prod.pickup_hint')} <a class="link font-semibold" href="/shop/pickup">{t('shop.nav.pickup')}</a></Alert>
		{#if !receiving.length}
			<div class="card card-body items-center py-10 text-center text-muted"><Truck size={28} /><p>{t('shop.prod.receive_empty')}</p></div>
		{/if}
		<div class="grid gap-4 {receiving.length > 1 ? 'xl:grid-cols-2' : ''}">
			{#each receiving as b (b.id)}
				<section class="card card-body gap-3">
					<div class="flex flex-wrap items-start justify-between gap-2">
						<div class="min-w-0">
							<h2 class="font-mono text-sm font-bold text-ink">{b.number}</h2>
							<p class="text-sm text-muted">{[b.supplier, b.reference].filter(Boolean).join(' · ') || t('shop.prod.no_supplier')}</p>
						</div>
						<span class="badge {batchBadge(b.status)}">{t(`shop.prod.status.${b.status}`)}</span>
					</div>
					<p class="font-mono text-xs text-muted">{t('shop.prod.received_counts', { got: b.received_quantity, qty: b.quantity })}</p>
					<ProductionReceive batch={b} />
				</section>
			{/each}
		</div>
		{#if data.received.length}
			<section class="card card-body gap-2" aria-labelledby="hist">
				<h2 id="hist" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.prod.history')}</h2>
				<div class="overflow-x-auto">
					<table class="table table-sm">
						<thead><tr><th>{t('shop.prod.batch')}</th><th>{t('shop.prod.supplier')}</th><th class="text-right">{t('shop.prod.qty')}</th><th>{t('shop.prod.received_at')}</th><th></th></tr></thead>
						<tbody>
							{#each data.received as b (b.id)}
								<tr>
									<td class="font-mono text-xs font-semibold">{b.number}</td>
									<td class="text-sm">{[b.supplier, b.reference].filter(Boolean).join(' · ') || '—'}</td>
									<td class="text-right font-mono text-sm">{b.received_quantity}/{b.quantity}</td>
									<td class="font-mono text-xs text-muted">{dateTime(b.received_at)}</td>
									<td class="text-right"><a class="btn btn-outline btn-xs gap-1" href={batchCsvHref(b.id)} download><Download size={12} /> {t('shop.prod.csv')}</a></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	{/if}
</div>

<Modal bind:open={createOpen} title={t('shop.prod.create_title')}>
	<form method="POST" action="?/createBatch" class="grid gap-3" use:enhance={closer(() => (createOpen = false))}>
		<input type="hidden" name="payload" value={JSON.stringify({ line_ids: selected, ...draft })} />
		<p class="text-sm text-muted">{t('shop.prod.create_body', { n: selected.length })}</p>
		<Field label={t('shop.prod.supplier')} bind:value={draft.supplier} error={errs.supplier} />
		<Field label={t('shop.prod.reference')} bind:value={draft.reference} error={errs.reference} />
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.prod.note')}</span></span>
			<textarea class="textarea textarea-bordered w-full" rows="3" bind:value={draft.note}></textarea>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (createOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Send size={16} /> {t('shop.prod.create_confirm')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={editOpen} title={t('shop.prod.edit_title')}>
	<form method="POST" action="?/editBatch" class="grid gap-3" use:enhance={closer(() => (editOpen = false))}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<Field label={t('shop.prod.supplier')} name="supplier" bind:value={draft.supplier} error={errs.supplier} />
		<Field label={t('shop.prod.reference')} name="reference" bind:value={draft.reference} error={errs.reference} />
		<label class="form-control w-full">
			<span class="label"><span class="label-text">{t('shop.prod.note')}</span></span>
			<textarea class="textarea textarea-bordered w-full" name="note" rows="3" bind:value={draft.note}></textarea>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (editOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('shop.prod.delete_title')}>
	<form method="POST" action="?/deleteBatch" class="grid gap-3" use:enhance={closer(() => (delOpen = false))}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.prod.delete_body', { number: target?.number ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
