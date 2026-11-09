<script lang="ts">
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, ClipboardCheck, Printer } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { stockLabel, type StockRow } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();

	const key = (r: StockRow) => r.product_id + ':' + (r.variant_id ?? '');
	let q = $state('');
	let category = $state('');
	let onlyDiff = $state(false);
	let counts = $state<Record<string, string>>({});
	let notes = $state<Record<string, string>>({});
	let overall = $state('');
	let open = $state(false);
	let busy = $state(false);

	const categories = $derived([...new Map(data.rows.filter((r) => r.category_id).map((r) => [r.category_id as string, r.category_name ?? ''])).entries()].sort((a, b) => a[1].localeCompare(b[1])));
	const hasUncategorised = $derived(data.rows.some((r) => !r.category_id));
	const entered = (r: StockRow) => (counts[key(r)] ?? '').trim() !== '';
	const diff = (r: StockRow) => (entered(r) ? Number(counts[key(r)]) - r.on_hand : 0);
	const valid = (r: StockRow) => !entered(r) || (Number.isInteger(Number(counts[key(r)])) && Number(counts[key(r)]) >= 0);
	const visible = $derived(
		data.rows.filter((r) => {
			const text = q.toLowerCase();
			if (text && !stockLabel(r).toLowerCase().includes(text)) return false;
			if (category && (category === 'none' ? r.category_id : r.category_id !== category)) return false;
			return !onlyDiff || diff(r) !== 0;
		})
	);
	const countedRows = $derived(data.rows.filter(entered));
	const changed = $derived(countedRows.filter((r) => diff(r) !== 0));
	const totalDiff = $derived(changed.reduce((n, r) => n + diff(r), 0));
	const allValid = $derived(data.rows.every(valid));
	const dirty = $derived(countedRows.length > 0 || overall.trim() !== '');
	const sign = (n: number) => (n > 0 ? '+' : '') + n;

	const printHref = $derived.by(() => {
		const sp = new URLSearchParams();
		if (q) sp.set('q', q);
		if (category) sp.set('category', category);
		const qs = sp.toString();
		// The PDF sheet is generated on the server with the same filters as this screen and opens straight in the browser's PDF viewer.
		return '/api/v1/shop/inventory/count-sheet.pdf' + (qs ? `?${qs}` : '');
	});
	const payload = $derived(JSON.stringify({ note: overall.trim(), counts: countedRows.map((r) => ({ product_id: r.product_id, variant_id: r.variant_id, counted: Number(counts[key(r)]), note: (notes[key(r)] ?? '').trim() })) }));
	const result = $derived(form && 'result' in form ? form.result : null);
	const errorText = $derived(form && 'status' in form && form.status ? String(form.message || t('common.error.generic')) : '');

	function reset() {
		counts = {};
		notes = {};
		overall = '';
		open = false;
	}
	function next(e: KeyboardEvent) {
		if (e.key !== 'Enter') return;
		e.preventDefault();
		const inputs = [...document.querySelectorAll<HTMLInputElement>('input[data-count]')];
		inputs[inputs.indexOf(e.currentTarget as HTMLInputElement) + 1]?.focus();
	}

	let leaving = $state(false);
	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});

	usePageHeader(() => ({ title: t('shop.count.title'), context: t('shop.nav.inventory'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/inventory"><ArrowLeft size={16} /> {t('shop.inv.back')}</a>
	<a class="btn btn-outline gap-2" href={printHref} target="_blank" rel="noopener"><Printer size={16} /> {t('shop.count.print_sheet')}</a>
	<button class="btn btn-primary gap-2" type="button" disabled={!changed.length || !allValid || busy} onclick={() => (open = true)}><ClipboardCheck size={16} /> {t('shop.count.apply')}</button>
{/snippet}

<svelte:window onbeforeunload={(e) => dirty && !busy && e.preventDefault()} />
<svelte:head><title>{pageTitle(t('shop.count.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={q} searchLabel={t('shop.product.search')} searchPlaceholder={t('shop.product.search_placeholder')} onsearch={(v) => (q = v)} showClear={!!q || !!category || onlyDiff} onclear={() => { q = ''; category = ''; onlyDiff = false; }}>
		{#if categories.length}
			<select class="select w-auto" aria-label={t('shop.product.f.category')} bind:value={category}>
				<option value="">{t('shop.count.all_categories')}</option>
				{#each categories as [id, name] (id)}<option value={id}>{name}</option>{/each}
				{#if hasUncategorised}<option value="none">{t('shop.count.no_category')}</option>{/if}
			</select>
		{/if}
		<label class="flex h-9 items-center gap-2 text-sm"><input type="checkbox" class="checkbox" bind:checked={onlyDiff} /> {t('shop.count.only_diff')}</label>
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		<p class="text-sm text-muted">{t('shop.count.hint')}</p>
		{#if result}<Alert kind="success">{t('shop.count.done', { applied: result.applied, unchanged: result.unchanged })}</Alert>{/if}
		{#if errorText && !open}<Alert kind="error" title={t('shop.action_failed')}>{errorText}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !visible.length}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{data.rows.length ? t('shop.count.empty') : t('shop.inv.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('shop.col.product')}</th>
							<th>{t('shop.product.f.category')}</th>
							<th class="text-right">{t('shop.inv.on_hand')}</th>
							<th class="w-36 text-right">{t('shop.count.counted')}</th>
							<th class="w-28 text-right">{t('shop.count.difference')}</th>
							<th class="w-64">{t('shop.count.row_note')}</th>
						</tr>
					</thead>
					<tbody>
						{#each visible as r (key(r))}
							{@const d = diff(r)}
							<tr class="hover:bg-surface">
								<td class="font-semibold">{r.product_name}{#if r.variant_name}<span class="ml-1.5 font-normal text-muted">- {r.variant_name}</span>{/if}</td>
								<td class="text-sm text-muted">{r.category_name ?? ''}</td>
								<td class="text-right font-mono text-sm font-bold">{r.on_hand}</td>
								<td class="text-right">
									<input class="input ml-auto w-24 text-right font-mono {valid(r) ? '' : 'input-error'}" type="number" inputmode="numeric" min="0" step="1" data-count aria-label={t('shop.count.counted') + ': ' + stockLabel(r)} value={counts[key(r)] ?? ''} oninput={(e) => (counts[key(r)] = e.currentTarget.value)} onkeydown={next} />
								</td>
								<td class="text-right font-mono text-sm font-bold {!entered(r) ? 'text-dim' : d === 0 ? 'text-success' : d < 0 ? 'text-error' : 'text-warning'}">{entered(r) ? (d === 0 ? '0' : sign(d)) : '-'}</td>
								<td><input class="input" type="text" maxlength="255" placeholder={t('shop.count.note_ph')} aria-label={t('shop.count.row_note') + ': ' + stockLabel(r)} value={notes[key(r)] ?? ''} oninput={(e) => (notes[key(r)] = e.currentTarget.value)} /></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<div class="grid gap-3 md:grid-cols-[1fr_auto] md:items-start">
				<label class="grid gap-1.5 text-sm"><span class="label-text">{t('shop.count.overall_note')}</span><textarea class="textarea" maxlength="255" rows="2" bind:value={overall}></textarea></label>
				<p class="font-mono text-sm text-muted md:pt-7">{t('shop.count.summary', { n: countedRows.length, m: changed.length })}</p>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open title={t('shop.count.confirm_title')}>
	<form method="POST" action="?/apply" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type === 'success') reset(); }; }}>
		<input type="hidden" name="payload" value={payload} />
		<p class="text-sm text-muted">{t('shop.count.confirm_help')}</p>
		{#if errorText}<Alert kind="error" title={t('shop.action_failed')}>{errorText}</Alert>{/if}
		<ul class="grid max-h-80 gap-1 overflow-y-auto text-sm">
			{#each changed as r (key(r))}
				<li class="flex items-baseline justify-between gap-4"><span>{stockLabel(r)}</span><span class="font-mono whitespace-nowrap">{r.on_hand} &rarr; {counts[key(r)]} <b class={diff(r) < 0 ? 'text-error' : 'text-warning'}>({sign(diff(r))})</b></span></li>
			{/each}
		</ul>
		<p class="border-t border-line pt-2 text-sm font-semibold">{t('shop.count.total')}: <span class="font-mono">{sign(totalDiff)}</span></p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy || !changed.length}>{t('shop.count.apply')}</button>
		</div>
	</form>
</Modal>
