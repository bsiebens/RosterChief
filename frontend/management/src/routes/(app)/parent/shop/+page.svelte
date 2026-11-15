<script lang="ts">
	import { Clock, Download, Paperclip, Package, ReceiptText, Search, ShoppingBag, ShoppingCart, Trash2 } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { formatDay, parseIsoDate } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { CartLine, OrderHistory, ShopCatalog, ShopProduct, ShopReceipt } from '#lib/portal-shop.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	let updatedCatalog = $state<ShopCatalog | null>(null);
	const catalog = $derived(updatedCatalog ?? data.catalog);
	const storageKey = $derived(`rosterchief:shop:${catalog.viewer_id}`);
	let q = $state('');
	let category = $state('');
	const categories = $derived([...new Set(catalog.products.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b)));
	const products = $derived(catalog.products.filter((item) =>
		(!category || item.category === category) && (!q.trim() || `${item.name} ${item.description}`.toLocaleLowerCase().includes(q.trim().toLocaleLowerCase()))
	));

	type Draft = { variant: string; beneficiary: string; quantity: number; name: string; number: string };
	const defaultDraft = (product: ShopProduct): Draft => ({
		variant: product.variants.length === 1 && product.variants[0].in_stock ? product.variants[0].id : '',
		beneficiary: '', quantity: 1, name: '', number: ''
	});
	// A product with nothing to choose goes straight into the cart; otherwise "Choose" opens its options in a dialog.
	const needsOptions = (product: ShopProduct) => product.variants.length > 1 || (product.variants.length === 1 && !product.variants[0].in_stock) || product.personalization_enabled || catalog.people.length > 1;
	let busy = $state(false);
	let view = $state<'shop' | 'orders'>('shop');
	let choosing = $state<ShopProduct | null>(null);
	let draft = $state<Draft>({ variant: '', beneficiary: '', quantity: 1, name: '', number: '' });
	let optionsDialog = $state<HTMLDialogElement>();
	const chosenVariant = $derived(choosing?.variants.find((item) => item.id === draft.variant));
	const canAdd = $derived(!!choosing && !busy && choosing.in_stock && (!choosing.variants.length || !!chosenVariant?.in_stock) && Number.isInteger(draft.quantity) && draft.quantity >= 1 && draft.quantity <= 999);
	function choose(product: ShopProduct) {
		if (!needsOptions(product)) {
			addLine(product, defaultDraft(product));
			return;
		}
		choosing = product;
		draft = defaultDraft(product);
		optionsDialog?.showModal();
	}
	// The soonest order-by date over what is on sale, shown as the strip on top.
	const nextWindow = $derived(
		catalog.products
			.map((item) => item.next_ordering_window)
			.filter((item): item is NonNullable<typeof item> => !!item)
			.sort((a, b) => a.deadline.localeCompare(b.deadline))[0] ?? null
	);

	let cart = $state<CartLine[]>([]);
	let discount = $state('');
	let checkoutId = $state('');
	let restored = $state(false);
	let loadingOrders = $state(false);
	let placed = $state<ShopReceipt[]>([]);
	const latest = $derived(placed[0] ?? null);
	let older = $state<ShopReceipt[]>([]);
	let olderMore = $state<boolean | null>(null);
	let cartError = $state('');
	let orderError = $state('');
	const receipts = $derived([
		...placed,
		...(data.orders?.results ?? []),
		...older
	].filter((item, index, all) => all.findIndex((entry) => entry.id === item.id) === index));
	const more = $derived(olderMore ?? data.orders?.more ?? false);
	const subtotal = $derived(cart.reduce((sum, line) => sum + Number(line.unit_price) * line.quantity, 0));
	const buyerName = (id: string | null) => catalog.people.find((person) => person.id === id)?.name;
	const productById = $derived(new Map(catalog.products.map((product) => [product.id, product])));
	const label = (line: { name: string; variant: string }) => [line.name, line.variant].filter(Boolean).join(' · ');
	const calendarDay = (value: string) => formatDay(parseIsoDate(value) ?? value);
	const statusText = (status: string) => t(['in_production', 'completed'].includes(status) ? `portal.shop.${status}` : `shop.orders.fulfillment.${status}`);

	onMount(() => {
		try {
			const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
			if (Array.isArray(saved?.cart)) {
				cart = saved.cart.filter((item: CartLine) => item && typeof item.id === 'string' && typeof item.product_id === 'string' && Number.isFinite(Number(item.unit_price)) && Number.isInteger(item.quantity) && item.quantity >= 1 && item.quantity <= 999).slice(0, 100);
				discount = typeof saved.discount === 'string' ? saved.discount : '';
				checkoutId = typeof saved.checkoutId === 'string' ? saved.checkoutId : '';
			}
		} catch {
			// Cart remains usable when local storage is unavailable.
		}
		restored = true;
	});

	$effect(() => {
		if (!restored) return;
		try {
			localStorage.setItem(storageKey, JSON.stringify({ cart, discount, checkoutId }));
		} catch {
			// A session can still check out its in-memory cart.
		}
	});

	function updateCart(lines: CartLine[]) {
		cart = lines;
		checkoutId = '';
		cartError = '';
	}

	function addLine(product: ShopProduct, choice: Draft) {
		if (cart.length >= 100) {
			cartError = t('portal.shop.cart_full');
			return;
		}
		const variant = product.variants.find((item) => item.id === choice.variant);
		if (!product.in_stock || (product.variants.length && !variant?.in_stock) || !Number.isInteger(choice.quantity) || choice.quantity < 1 || choice.quantity > 999) return;
		updateCart([...cart, {
			id: crypto.randomUUID(),
			name: product.name,
			variant: variant?.name ?? '',
			product_id: product.id,
			variant_id: variant?.id ?? null,
			beneficiary_id: choice.beneficiary || null,
			quantity: choice.quantity,
			unit_price: variant?.effective_price ?? product.price,
			ordering_deadline: product.next_ordering_window?.deadline ?? null,
			estimated_delivery: product.next_ordering_window?.estimated_delivery ?? null,
			personalization_name: choice.name.trim(),
			personalization_number: choice.number.trim()
		}]);
	}
	function addChosen() {
		if (!choosing || !canAdd) return;
		addLine(choosing, draft);
		optionsDialog?.close();
	}

	function currentLine(line: CartLine) {
		const product = productById.get(line.product_id);
		const variant = product?.variants.find((item) => item.id === line.variant_id);
		if (!product?.in_stock || (product.variants.length && !variant?.in_stock) || (line.variant_id && !variant)) return null;
		return {
			...line,
			name: product.name,
			variant: variant?.name ?? '',
			unit_price: variant?.effective_price ?? product.price,
			ordering_deadline: product.next_ordering_window?.deadline ?? null,
			estimated_delivery: product.next_ordering_window?.estimated_delivery ?? null
		};
	}

	const stale = $derived(catalog.open && cart.some((line) => {
		const current = currentLine(line);
		return current && (current.unit_price !== line.unit_price || current.ordering_deadline !== line.ordering_deadline || current.estimated_delivery !== line.estimated_delivery);
	}));
	const unavailable = $derived(catalog.open && cart.some((line) => !currentLine(line)));

	async function checkout(event: SubmitEvent) {
		event.preventDefault();
		if (busy || !cart.length || stale || unavailable || !catalog.open) return;
		busy = true;
		cartError = '';
		checkoutId ||= crypto.randomUUID();
		const lines = cart.map(({ id: _id, name: _name, variant: _variant, ...line }) => line);
		const result = await api<ShopReceipt>('POST', '/portal/shop/orders', { body: { checkout_id: checkoutId, lines, discount_code: discount.trim() } });
		if (result.ok && result.data) {
			placed = [result.data, ...placed];
			updateCart([]);
			discount = '';
		} else if (result.code === 'cart_changed' || result.code === 'insufficient_stock' || result.code === 'shop_closed') {
			const refreshed = await api<ShopCatalog>('GET', '/portal/shop');
			if (refreshed.ok && refreshed.data) updatedCatalog = refreshed.data;
			cartError = result.code === 'cart_changed' ? t('portal.shop.changed') : result.message || t('common.error.generic');
		} else {
			cartError = result.fields.discount_code || result.message || t('common.error.generic');
		}
		busy = false;
	}

	async function loadOlder() {
		loadingOrders = true;
		const result = await api<OrderHistory>('GET', '/portal/shop/orders', { query: { offset: receipts.length } });
		if (result.ok && result.data) {
			older = [...older, ...result.data.results];
			olderMore = result.data.more;
			orderError = '';
		} else {
			orderError = result.message || t('common.error.generic');
		}
		loadingOrders = false;
	}

	usePageHeader(() => ({ title: t('portal.shop'), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-outline gap-2" type="button" aria-pressed={view === 'orders'} onclick={() => (view = view === 'orders' ? 'shop' : 'orders')}><ReceiptText size={16} aria-hidden="true" /> {view === 'orders' ? t('portal.shop.products') : t('portal.shop.orders')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('portal.shop'))}</title></svelte:head>

<div class="grid w-full min-w-0 gap-4 sm:gap-5">
	{#if !catalog.open}
		<Alert kind="warning">{catalog.closed_message || t('portal.shop.closed')}</Alert>
	{/if}
	{#if latest}<Alert kind="success">{t('portal.shop.placed', { number: latest.number })}</Alert>{/if}

	<div class="grid items-start gap-5 md:grid-cols-[minmax(0,2fr)_minmax(17rem,1fr)] xl:grid-cols-[minmax(0,1fr)_22rem]">
		<div class="grid min-w-0 gap-5">
			{#if catalog.open && view === 'shop'}
				{#if nextWindow}
					<p class="flex items-center gap-2 rounded-box border border-info-border bg-info-bg px-4 py-2.5 text-sm text-info-text"><Clock size={16} class="shrink-0" aria-hidden="true" /><span>{t('shop.window.summary', { deadline: calendarDay(nextWindow.deadline), estimated_delivery: calendarDay(nextWindow.estimated_delivery) })}</span></p>
				{/if}
				<div class="flex flex-wrap items-center gap-2">
					{#if categories.length}
						<div class="inline-flex flex-wrap overflow-hidden rounded-box border border-line bg-paper" role="group" aria-label={t('portal.shop.all_categories')}>
							<button type="button" class="min-h-9 px-3.5 text-sm font-semibold {category === '' ? 'bg-ink text-white' : 'text-slate hover:bg-subhead'}" aria-pressed={category === ''} onclick={() => (category = '')}>{t('portal.shop.all_categories')}</button>
							{#each categories as name (name)}<button type="button" class="min-h-9 border-l border-line px-3.5 text-sm font-semibold {category === name ? 'bg-ink text-white' : 'text-slate hover:bg-subhead'}" aria-pressed={category === name} onclick={() => (category = name)}>{name}</button>{/each}
						</div>
					{/if}
					<label class="relative ml-auto w-full sm:w-64">
						<Search size={14} aria-hidden="true" class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" />
						<input class="input w-full pl-9" type="search" placeholder={t('portal.shop.search')} aria-label={t('portal.shop.search')} bind:value={q} />
					</label>
				</div>
				<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
					{#each products as product (product.id)}
						<article class="card flex flex-col overflow-hidden">
							{#if product.image_url}
								<img class="aspect-4/3 w-full object-cover" src={product.image_url} alt="" loading="lazy" />
							{:else}
								<div class="grid aspect-4/3 place-items-center bg-subhead text-dim"><Package size={32} aria-hidden="true" /></div>
							{/if}
							<div class="flex flex-1 flex-col justify-between gap-3 p-3.5">
								<div class="grid gap-1">
									<h3 class="font-semibold text-ink">{product.name}</h3>
									{#if product.description}<p class="line-clamp-2 text-xs whitespace-pre-line text-muted">{product.description}</p>{/if}
									{#if product.variants.length > 1}<p class="text-xs text-muted">{t('portal.shop.options_n', { n: product.variants.length })}</p>{/if}
								</div>
								<div class="flex items-center gap-2">
									<span class="font-display text-xl font-extrabold text-ink tabular-nums">{money(product.price)}</span>
									{#if product.in_stock}
										<button class="btn btn-primary btn-sm ml-auto" type="button" disabled={busy} onclick={() => choose(product)}>{needsOptions(product) ? t('portal.shop.choose') : t('portal.shop.add')}</button>
									{:else}
										<span class="badge badge-outline ml-auto">{t('shop.out_of_stock')}</span>
									{/if}
								</div>
							</div>
						</article>
					{:else}
						<p class="text-sm text-muted sm:col-span-2 xl:col-span-3">{t('portal.shop.no_products')}</p>
					{/each}
				</div>
			{/if}

			{#if view === 'orders'}
			<section class="card card-body scroll-mt-44 gap-3 xl:scroll-mt-20" id="orders">
				<h2 class="card-title"><ReceiptText size={16} aria-hidden="true" /> {t('portal.shop.orders')}</h2>
				{#if orderError || !data.orders}<Alert kind="error">{orderError || t('common.error.generic')}</Alert>{/if}
				{#each receipts as receipt (receipt.id)}
					<details class="rounded-box border border-line" open={latest?.id === receipt.id}>
						<summary class="cursor-pointer p-3">
							<span class="inline-flex flex-wrap items-center gap-x-3 gap-y-1 align-middle">
								<span class="font-semibold">{receipt.number}</span>
								<span class="text-sm text-muted">{formatDay(receipt.created)}</span>
								<span class="badge badge-outline badge-sm">{statusText(receipt.fulfillment_status)}</span>
								<span class="badge badge-outline badge-sm">{t(`shop.orders.payment.${receipt.payment_status}`)}</span>
							</span>
						</summary>
						<div class="grid gap-3 border-t border-line p-3 text-sm">
							<ul class="grid gap-1">
								{#each receipt.lines as line (line.id)}
									<li class="flex justify-between gap-3"><span>{line.quantity} × {label(line)}</span><span class="tabular-nums">{money(line.amount)}</span></li>
								{/each}
								{#each receipt.discounts as discount}
									<li class="flex justify-between gap-3"><span>{discount.description}</span><span class="tabular-nums">−{money(discount.amount)}</span></li>
								{/each}
							</ul>
							<dl class="grid grid-cols-[1fr_auto] gap-1 border-t border-line pt-2">
								<dt>{t('shop.orders.total')}</dt><dd class="font-semibold tabular-nums">{money(receipt.total)}</dd>
								{#if Number(receipt.due) > 0 && receipt.fulfillment_status !== 'cancelled'}<dt>{t('shop.orders.due')}</dt><dd class="tabular-nums">{money(receipt.due)}</dd>{/if}
							</dl>
							{#if receipt.pickup_instructions}<p class="whitespace-pre-line">{receipt.pickup_instructions}</p>{/if}
							{#if Number(receipt.due) > 0 && receipt.fulfillment_status !== 'cancelled' && catalog.payment_instructions}<p class="whitespace-pre-line">{catalog.payment_instructions}</p>{/if}
							{#if receipt.invoice_number}
								<a class="btn btn-outline btn-sm w-fit gap-2" href="/api/v1/portal/shop/orders/{receipt.id}/invoice.pdf"><Download size={14} aria-hidden="true" /> {t('portal.shop.invoice')}</a>
							{/if}
						</div>
					</details>
				{:else}
					{#if data.orders}<p class="text-sm text-muted">{t('portal.shop.no_orders')}</p>{/if}
				{/each}
				{#if more}<button class="btn btn-outline w-fit" type="button" disabled={loadingOrders} onclick={loadOlder}>{t('portal.shop.more')}</button>{/if}
			</section>
			{/if}
		</div>

		{#if catalog.open || cart.length}
			<section class="card scroll-mt-44 xl:sticky xl:top-20 xl:scroll-mt-20" id="cart" aria-live="polite">
				<div class="card-h"><ShoppingBag size={17} aria-hidden="true" /><h2>{t('portal.shop.cart')}</h2>{#if cart.length}<span class="badge badge-sm badge-outline ml-auto">{t('portal.shop.items', { n: cart.reduce((sum, line) => sum + line.quantity, 0) })}</span>{/if}</div>
				{#if cartError}<div class="p-4 pb-0"><Alert kind="error">{cartError}</Alert></div>{/if}
				{#if cart.length}
					<form onsubmit={checkout}>
						<ul class="divide-y divide-rule">
							{#each cart as line (line.id)}
								{@const image = productById.get(line.product_id)?.image_url}
								<li class="flex items-start gap-3 px-4 py-3">
									{#if image}<img class="size-14 shrink-0 rounded object-cover" src={image} alt="" />{:else}<div class="grid size-14 shrink-0 place-items-center rounded bg-subhead text-dim"><Package size={20} aria-hidden="true" /></div>{/if}
									<div class="min-w-0 flex-1">
										<p class="font-semibold text-ink">{line.name}</p>
										<p class="text-xs text-muted">{[line.variant, buyerName(line.beneficiary_id), [line.personalization_number && '#' + line.personalization_number, line.personalization_name].filter(Boolean).join(' ')].filter(Boolean).join(' · ')}</p>
										{#if catalog.open && !currentLine(line)}<p class="text-xs text-danger">{t('portal.shop.line_unavailable')}</p>{/if}
										<div class="mt-1.5 flex items-center gap-3">
											<span class="inline-flex items-center overflow-hidden rounded-full border border-line text-sm">
												<button type="button" class="grid size-7 place-items-center hover:bg-subhead disabled:opacity-40" disabled={busy || line.quantity <= 1} aria-label={t('portal.shop.quantity_for', { name: label(line) })} onclick={() => updateCart(cart.map((item) => item.id === line.id ? { ...item, quantity: item.quantity - 1 } : item))}>−</button>
												<span class="min-w-6 text-center font-semibold tabular-nums">{line.quantity}</span>
												<button type="button" class="grid size-7 place-items-center hover:bg-subhead disabled:opacity-40" disabled={busy || line.quantity >= 999} aria-label={t('portal.shop.quantity_for', { name: label(line) })} onclick={() => updateCart(cart.map((item) => item.id === line.id ? { ...item, quantity: item.quantity + 1 } : item))}>+</button>
											</span>
											<button type="button" class="link text-xs" disabled={busy} aria-label={t('portal.shop.remove', { name: label(line) })} onclick={() => updateCart(cart.filter((item) => item.id !== line.id))}>{t('portal.shop.remove_short')}</button>
										</div>
									</div>
									<b class="font-mono text-ink tabular-nums">{money(Number(line.unit_price) * line.quantity)}</b>
								</li>
							{/each}
						</ul>
						<div class="grid gap-3 border-t border-line p-4">
							{#if stale}
								<Alert kind="warning">{t('portal.shop.changed')}</Alert>
								<button class="btn btn-outline" type="button" onclick={() => updateCart(cart.map((item) => currentLine(item) ?? item))}>{t('portal.shop.update')}</button>
							{/if}
							{#if catalog.open}
								<label class="flex items-center gap-2"><span class="sr-only">{t('shop.orders.new.discount_code')}</span>
									<input class="input w-full" placeholder={t('shop.orders.new.discount_code')} disabled={busy} maxlength="255" value={discount} oninput={(e) => { discount = e.currentTarget.value; checkoutId = ''; }} />
								</label>
							{/if}
							<div class="flex justify-between text-sm"><span class="text-muted">{t('portal.shop.estimate')}</span><b class="font-mono text-ink tabular-nums">{money(subtotal)}</b></div>
							<button class="btn btn-primary w-full" type="submit" disabled={busy || !restored || !catalog.open || stale || unavailable}>{t('portal.shop.place')}</button>
							<p class="text-xs text-muted">{t('portal.shop.invoice_note')}</p>
						</div>
					</form>
				{:else}
					<p class="p-4 text-sm text-muted">{t('portal.shop.empty')}</p>
				{/if}
			</section>
		{/if}
	</div>
</div>

<dialog bind:this={optionsDialog} class="portal-modal" aria-label={choosing?.name ?? t('portal.shop.options')} onclose={() => (choosing = null)} onclick={(e) => { if (e.target === optionsDialog) optionsDialog?.close(); }}>
	{#if choosing}
		{@const product = choosing}
		<form class="grid gap-4 p-5" onsubmit={(e) => { e.preventDefault(); addChosen(); }}>
			<div class="flex items-start gap-3">
				{#if product.image_url}<img class="size-16 shrink-0 rounded object-cover" src={product.image_url} alt="" />{:else}<div class="grid size-16 shrink-0 place-items-center rounded bg-subhead text-dim"><Package size={24} aria-hidden="true" /></div>{/if}
				<div class="min-w-0 flex-1"><h2 class="font-display text-xl font-extrabold text-ink uppercase">{product.name}</h2>{#if product.description}<p class="line-clamp-3 text-sm whitespace-pre-line text-muted">{product.description}</p>{/if}</div>
				<b class="font-display text-xl text-ink tabular-nums">{money(chosenVariant?.effective_price ?? product.price)}</b>
			</div>
			{#if product.variants.length}
				<label class="grid gap-1 text-sm font-semibold">{t('portal.shop.option')}
					<select class="select w-full font-normal" bind:value={draft.variant} required>
						<option value="" disabled>{t('portal.shop.option')}</option>
						{#each product.variants as item (item.id)}<option value={item.id} disabled={!item.in_stock}>{item.name} · {money(item.effective_price)}{!item.in_stock ? ` · ${t('shop.out_of_stock')}` : ''}</option>{/each}
					</select>
				</label>
			{/if}
			{#if catalog.people.length > 1}
				<label class="grid gap-1 text-sm font-semibold">{t('portal.shop.recipient')}
					<select class="select w-full font-normal" bind:value={draft.beneficiary}>
						<option value="">{buyerName(catalog.viewer_id)}</option>
						{#each catalog.people.filter((item) => item.id !== catalog.viewer_id) as person (person.id)}<option value={person.id}>{person.name}</option>{/each}
					</select>
				</label>
			{/if}
			{#if product.personalization_enabled}
				<div class="grid gap-3 sm:grid-cols-2">
					<label class="grid gap-1 text-sm font-semibold">{t('shop.orders.line.pers_name')}<input class="input w-full font-normal" maxlength="100" bind:value={draft.name} /></label>
					<label class="grid gap-1 text-sm font-semibold">{t('shop.orders.line.pers_number')}<input class="input w-full font-normal" maxlength="20" bind:value={draft.number} /></label>
				</div>
			{/if}
			{#if product.next_ordering_window}
				<p class="flex items-center gap-2 rounded-box border border-info-border bg-info-bg px-3 py-2 text-xs text-info-text"><Clock size={14} class="shrink-0" aria-hidden="true" />{t('shop.window.summary', { deadline: calendarDay(product.next_ordering_window.deadline), estimated_delivery: calendarDay(product.next_ordering_window.estimated_delivery) })}</p>
			{/if}
			{#each product.files as file (file.url)}<a href={file.url} class="flex w-fit items-center gap-1.5 text-xs text-muted hover:text-ink" target="_blank" rel="noopener"><Paperclip size={13} aria-hidden="true" />{file.title}</a>{/each}
			<div class="flex items-center gap-3 border-t border-line pt-4">
				<label class="flex items-center gap-2 text-sm font-semibold">{t('portal.shop.quantity')}<input class="input w-20 font-normal" type="number" min="1" max="999" step="1" bind:value={draft.quantity} /></label>
				<span class="flex-1"></span>
				<button class="btn btn-outline" type="button" onclick={() => optionsDialog?.close()}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={!canAdd}>{t('portal.shop.add')}</button>
			</div>
		</form>
	{/if}
</dialog>
