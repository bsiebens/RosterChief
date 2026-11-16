<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, Check, ListOrdered, NotebookPen, Save, Tag, Ticket, UserRound, X, ClipboardList } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import OrderLinesEditor, { newLine, type LineDraft } from '#lib/components/shop/OrderLinesEditor.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { browserApi, money, type OrderIn, type Preview } from '#lib/shop-orders.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();

	let purchaser = $state('');
	let lines = $state<LineDraft[]>([newLine()]);
	let discountCode = $state('');
	let notes = $state('');
	let vouchers = $state<{ number: string; amount: string; available: string }[]>([]);
	let voucherNumber = $state('');
	let voucherErr = $state('');
	let voucherBusy = $state(false);

	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);

	const valid = $derived(lines.filter((l) => l.product_id && l.quantity >= 1 && (!variantsNeeded(l) || l.variant_id)));
	function variantsNeeded(l: LineDraft) {
		return !!data.products.find((p) => p.id === l.product_id)?.variants.some((v) => v.is_active);
	}
	const dirty = $derived(!!purchaser || !!discountCode || !!notes || vouchers.length > 0 || lines.some((l) => l.product_id));

	// Live prices: the server prices the lines (and the discount code) as soon as the input changes; until then a client estimate shows.
	let preview = $state<Preview | null>(null);
	let discountErr = $state('');
	let previewFailed = $state(false);
	let seqNo = 0;
	const lineIn = (l: LineDraft) => ({ product_id: l.product_id, variant_id: l.variant_id || null, quantity: l.quantity, beneficiary_id: l.beneficiary_id || null, personalization_number: l.personalization_number, personalization_name: l.personalization_name });
	$effect(() => {
		const body = { purchaser_id: purchaser, lines: valid.map(lineIn), discount_code: discountCode.trim() || undefined, notes: undefined };
		const ready = !!purchaser && valid.length > 0 && valid.length === lines.length;
		const mine = ++seqNo;
		preview = null;
		if (!ready) {
			untrack(() => {
				preview = null;
				discountErr = '';
				previewFailed = false;
			});
			return;
		}
		const timer = setTimeout(async () => {
			const res = await browserApi<Preview>('POST', '/shop/orders/preview', body);
			if (mine !== seqNo) return;
			if (res.ok) {
				preview = res.data ?? null;
				discountErr = '';
				previewFailed = false;
			} else {
				discountErr = res.fields.discount_code ?? '';
				previewFailed = !res.fields.discount_code;
				preview = null;
			}
		}, 350);
		return () => clearTimeout(timer);
	});
	const unit = (l: LineDraft) => {
		const p = data.products.find((x) => x.id === l.product_id);
		const v = p?.variants.find((x) => x.id === l.variant_id);
		return Number(v ? v.effective_price : (p?.price ?? 0)) || 0;
	};
	const estimateTotal = $derived(valid.reduce((s, l) => s + unit(l) * l.quantity, 0));
	const total = $derived(preview ? Number(preview.total) : estimateTotal);
	const voucherSum = $derived(vouchers.reduce((s, v) => s + (Number(v.amount) || 0), 0));
	const due = $derived(Math.max(0, total - voucherSum));

	async function addVoucher() {
		const number = voucherNumber.trim().toUpperCase();
		voucherErr = '';
		if (!number) return;
		if (vouchers.some((v) => v.number === number)) {
			voucherErr = t('shop.orders.new.voucher_dup');
			return;
		}
		voucherBusy = true;
		const res = await browserApi<{ number: string; available_amount: string; status: string }>('GET', `/shop/vouchers/lookup?number=${encodeURIComponent(number)}`);
		voucherBusy = false;
		if (!res.ok || !res.data) {
			voucherErr = res.status === 404 ? t('shop.orders.new.voucher_unknown') : t('common.error.generic');
			return;
		}
		if (res.data.status !== 'usable' || Number(res.data.available_amount) <= 0) {
			voucherErr = t('shop.orders.new.voucher_unusable');
			return;
		}
		const left = Math.max(0, total - voucherSum);
		const amount = Math.min(left, Number(res.data.available_amount));
		vouchers = [...vouchers, { number: res.data.number, amount: amount.toFixed(2), available: res.data.available_amount }];
		voucherNumber = '';
	}

	const payload = $derived(
		JSON.stringify({ purchaser_id: purchaser, lines: valid.map(lineIn), discount_code: discountCode.trim(), notes, vouchers: vouchers.filter((v) => Number(v.amount) > 0).map((v) => ({ number: v.number, amount: v.amount })) } satisfies OrderIn)
	);

	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status) return '';
		if (form.code === 'insufficient_stock') return form.message ? String(form.message) : t('shop.orders.err.insufficient_stock');
		return form.message ? String(form.message) : form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed');
	});

	usePageHeader(() => ({ title: t('shop.orders.title_new'), context: t('shop.orders.title'), actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/orders"><ArrowLeft size={16} /> {t('shop.orders.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.orders.title_new'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!purchaser || !valid.length || valid.length !== lines.length) {
			tried = true;
			cancel();
			return;
		}
		busy = true;
		leaving = true;
		return async ({ update, result }) => {
			await update({ reset: false });
			busy = false;
			if (result.type !== 'redirect') leaving = false;
		};
	}}>
	<input type="hidden" name="payload" value={payload} />
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	{#if data.error}<Alert kind="error">{t('shop.err.failed')}</Alert>{/if}
	{#if !data.shopOpen}<Alert kind="info">{t('shop.orders.new.shop_closed')}</Alert>{/if}
	{#if previewFailed}<Alert kind="warning">{t('shop.orders.new.preview_failed')}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><UserRound size={16} aria-hidden="true" />{t('shop.orders.card.purchaser')}</h2>
				<EntityPicker kind="member" name="purchaser_id_picker" label={t('shop.orders.col.purchaser')} bind:value={purchaser} error={errs.purchaser_id ?? (tried && !purchaser ? t('shop.orders.new.purchaser_required') : undefined)} />
			</section>
			<section class="card card-body card-hl gap-3">
				<header class="grid gap-0.5">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} aria-hidden="true" />{t('shop.orders.card.lines')}</h2>
					<p class="text-xs text-muted">{t('shop.orders.new.lines_help')}</p>
				</header>
				{#if tried && (!valid.length || valid.length !== lines.length)}<p class="text-xs text-error">{t('shop.orders.new.lines_required')}</p>{/if}
				<OrderLinesEditor products={data.products} bind:lines preview={preview?.lines ?? null} estimate={!preview} errors={errs} />
			</section>
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><NotebookPen size={16} aria-hidden="true" />{t('shop.orders.notes')}</h2>
				<textarea class="textarea textarea-bordered w-full" rows="3" bind:value={notes} aria-label={t('shop.orders.notes')}></textarea>
			</section>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Tag size={16} aria-hidden="true" />{t('shop.orders.new.discount')}</h2>
				<Field label={t('shop.orders.new.discount_code')} name="discount_code_input" bind:value={discountCode} error={discountErr || errs.discount_code} autocomplete="off" />
				{#if preview?.discount}<p class="flex items-center gap-1.5 text-sm text-success"><Check size={14} aria-hidden="true" /> {preview.discount.description}: −{money(preview.discount.amount)}</p>{/if}

				<h3 class="mt-2 flex items-center gap-2 font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase"><Ticket size={14} aria-hidden="true" /> {t('shop.orders.new.vouchers')}</h3>
				<div class="flex gap-2">
					<input class="input input-bordered min-w-0 flex-1" bind:value={voucherNumber} placeholder="VCH-2026-00001" aria-label={t('shop.pay.voucher_number')} onkeydown={(e) => e.key === 'Enter' && (e.preventDefault(), addVoucher())} />
					<button class="btn btn-outline" type="button" disabled={voucherBusy} onclick={addVoucher}>{t('shop.orders.new.voucher_add')}</button>
				</div>
				{#if voucherErr}<p class="text-xs text-error">{voucherErr}</p>{/if}
				{#if errs.voucher_number}<p class="text-xs text-error">{errs.voucher_number}</p>{/if}
				{#each vouchers as v (v.number)}
					<div class="flex items-center gap-2 rounded-lg border border-line px-3 py-2">
						<div class="min-w-0 flex-1"><div class="font-mono text-xs font-semibold">{v.number}</div><div class="text-xs text-muted">{t('shop.orders.new.voucher_available', { amount: money(v.available) })}</div></div>
						<input class="input input-bordered input-sm w-24 text-right font-mono" type="number" step="0.01" min="0" max={v.available} bind:value={v.amount} aria-label={t('shop.pay.amount')} />
						<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('common.delete')} onclick={() => (vouchers = vouchers.filter((x) => x.number !== v.number))}><X size={14} /></button>
					</div>
				{/each}
			</section>

			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" />{t('shop.orders.new.summary')}</h2>
				<dl class="grid gap-1.5 text-sm">
					{#each valid as line, i (line.key)}
						{@const product = data.products.find((item) => item.id === line.product_id)}
						{@const variant = product?.variants.find((item) => item.id === line.variant_id)}
						<div class="flex justify-between gap-3">
							<dt><span class="font-mono text-muted">{line.quantity} ×</span> {product?.name}{variant ? ` · ${variant.name}` : ''}</dt>
							<dd class="shrink-0 font-mono">{money(preview?.lines[i]?.line_total ?? unit(line) * line.quantity)}</dd>
						</div>
					{/each}
					<div class="flex justify-between gap-3 border-t border-line pt-1"><dt class="text-muted">{t('shop.orders.subtotal')}</dt><dd class="font-mono">{money(preview ? preview.subtotal : estimateTotal)}</dd></div>
					{#if preview?.discount}<div class="flex justify-between gap-3"><dt class="text-muted">{t('shop.orders.discount')} · {preview.discount.description}</dt><dd class="shrink-0 font-mono">−{money(preview.discount.amount)}</dd></div>{/if}
					<div class="flex justify-between gap-3 text-base font-semibold"><dt>{t('shop.orders.total')}</dt><dd class="font-mono">{money(total)}</dd></div>
					{#each vouchers.filter((voucher) => Number(voucher.amount) > 0) as voucher (voucher.number)}
						<div class="flex justify-between gap-3"><dt class="text-muted">{t('shop.orders.new.paid_by_voucher')} · {voucher.number}</dt><dd class="shrink-0 font-mono">−{money(voucher.amount)}</dd></div>
					{/each}
					{#if voucherSum > 0}<div class="flex justify-between gap-3 border-t border-line pt-1 text-base font-semibold"><dt>{t('shop.receipt.balance')}</dt><dd class="font-mono">{money(due)}</dd></div>{/if}
				</dl>
				{#if valid.length && !preview}<p class="text-xs text-muted">{t('shop.orders.new.estimate_hint')}</p>{/if}
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.orders.new.save')}</button>
					<a class="btn btn-outline w-full" href="/shop/orders">{t('common.cancel')}</a>
				</div>
			</section>
		</aside>
	</div>
</form>
