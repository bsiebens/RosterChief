<script lang="ts">
	import { Clock, Download, Package, ReceiptText, Search, ShoppingBag, ShoppingCart, Trash2 } from '@lucide/svelte';
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
	let drafts = $state<Record<string, Draft>>({});
	const draftFor = (product: ShopProduct): Draft => drafts[product.id] ?? {
		variant: product.variants.length === 1 && product.variants[0].in_stock ? product.variants[0].id : '',
		beneficiary: '', quantity: 1, name: '', number: ''
	};
	const changeDraft = (product: ShopProduct, values: Partial<Draft>) => (drafts[product.id] = { ...draftFor(product), ...values });

	let cart = $state<CartLine[]>([]);
	let discount = $state('');
	let checkoutId = $state('');
	let restored = $state(false);
	let busy = $state(false);
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

	function add(product: ShopProduct) {
		if (cart.length >= 100) {
			cartError = t('portal.shop.cart_full');
			return;
		}
		const draft = draftFor(product);
		const variant = product.variants.find((item) => item.id === draft.variant);
		if (!product.in_stock || (product.variants.length && !variant?.in_stock) || !Number.isInteger(draft.quantity) || draft.quantity < 1 || draft.quantity > 999) return;
		updateCart([...cart, {
			id: crypto.randomUUID(),
			name: product.name,
			variant: variant?.name ?? '',
			product_id: product.id,
			variant_id: variant?.id ?? null,
			beneficiary_id: draft.beneficiary || null,
			quantity: draft.quantity,
			unit_price: variant?.effective_price ?? product.price,
			ordering_deadline: product.next_ordering_window?.deadline ?? null,
			estimated_delivery: product.next_ordering_window?.estimated_delivery ?? null,
			personalization_name: draft.name.trim(),
			personalization_number: draft.number.trim()
		}]);
		changeDraft(product, { quantity: 1, name: '', number: '' });
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
	<a class="btn btn-outline gap-2" href="#orders"><ReceiptText size={16} aria-hidden="true" /> {t('portal.shop.orders')}</a>
	{#if catalog.open || cart.length}
		<a class="btn btn-outline gap-2" href="#cart"><ShoppingCart size={16} aria-hidden="true" /> {t('portal.shop.cart')}{#if cart.length}<span class="badge badge-sm badge-outline">{cart.length}</span>{/if}</a>
	{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('portal.shop'))}</title></svelte:head>

<div class="grid w-full min-w-0 gap-4 sm:gap-5">
	{#if !catalog.open}
		<Alert kind="warning">{catalog.closed_message || t('portal.shop.closed')}</Alert>
	{/if}
	{#if latest}<Alert kind="success">{t('portal.shop.placed', { number: latest.number })}</Alert>{/if}

	<div class="grid items-start gap-5 md:grid-cols-[minmax(0,2fr)_minmax(17rem,1fr)] xl:grid-cols-[minmax(0,2fr)_minmax(19rem,1fr)]">
		<div class="grid min-w-0 gap-5">
			{#if catalog.open}
				<section class="card card-body gap-4">
					<h2 class="card-title"><ShoppingBag size={16} aria-hidden="true" /> {t('portal.shop.products')}</h2>
					<div class="grid gap-2">
						<label class="relative">
							<Search size={16} aria-hidden="true" class="pointer-events-none absolute top-3.5 left-3 text-muted" />
							<input class="input input-bordered w-full pl-9" type="search" placeholder={t('portal.shop.search')} aria-label={t('portal.shop.search')} bind:value={q} />
						</label>
					</div>
					{#if categories.length}
						<div class="flex flex-wrap gap-1.5" role="group" aria-label={t('portal.shop.all_categories')}>
							<button type="button" class="btn btn-sm {category === '' ? '' : 'btn-outline'}" aria-pressed={category === ''} onclick={() => (category = '')}>{t('portal.shop.all_categories')}</button>
							{#each categories as name (name)}<button type="button" class="btn btn-sm {category === name ? '' : 'btn-outline'}" aria-pressed={category === name} onclick={() => (category = name)}>{name}</button>{/each}
						</div>
					{/if}
					<div class="grid items-start gap-4 sm:max-md:grid-cols-2 lg:grid-cols-2">
						{#each products as product (product.id)}
							{@const draft = draftFor(product)}
							{@const variant = product.variants.find((item) => item.id === draft.variant)}
							<article class="grid gap-3 overflow-hidden rounded-box border border-line">
								{#if product.image_url}
									<img class="aspect-video w-full object-cover" src={product.image_url} alt="" loading="lazy" />
								{:else}
									<div class="grid aspect-[3/1] place-items-center bg-surface text-muted"><Package size={28} aria-hidden="true" /></div>
								{/if}
								<div class="grid gap-2 px-3 pb-3">
									<div class="flex flex-wrap items-baseline justify-between gap-2">
										<h3 class="font-semibold">{product.name}</h3>
										<span class="font-display text-xl font-extrabold text-ink tabular-nums">{money(variant?.effective_price ?? product.price)}</span>
									</div>
									{#if product.description}<p class="whitespace-pre-line text-sm text-muted">{product.description}</p>{/if}
									{#if product.next_ordering_window}
										<p class="flex items-center gap-2 rounded-box border border-info-border bg-info-bg px-3 py-1.5 text-xs font-medium text-info-text"><Clock size={14} class="shrink-0" aria-hidden="true" />{t('shop.window.summary', { deadline: calendarDay(product.next_ordering_window.deadline), estimated_delivery: calendarDay(product.next_ordering_window.estimated_delivery) })}</p>
									{/if}
									{#each product.files as file (file.url)}
										<a href={file.url} class="link text-sm" target="_blank" rel="noopener">{file.title}</a>
									{/each}
									{#if product.in_stock}
										{#if product.variants.length}
											<label class="grid gap-1 text-sm">{t('portal.shop.option')}
												<select class="select select-bordered w-full" value={draft.variant} onchange={(e) => changeDraft(product, { variant: e.currentTarget.value })}>
													<option value="" disabled>{t('portal.shop.option')}</option>
													{#each product.variants as item (item.id)}
														<option value={item.id} disabled={!item.in_stock}>{item.name} · {money(item.effective_price)}{!item.in_stock ? ` · ${t('shop.out_of_stock')}` : ''}</option>
													{/each}
												</select>
											</label>
										{/if}
										{#if catalog.people.length > 1}
											<label class="grid gap-1 text-sm">{t('portal.shop.recipient')}
												<select class="select select-bordered w-full" value={draft.beneficiary} onchange={(e) => changeDraft(product, { beneficiary: e.currentTarget.value })}>
													<option value="">{buyerName(catalog.viewer_id)}</option>
													{#each catalog.people.filter((item) => item.id !== catalog.viewer_id) as person (person.id)}<option value={person.id}>{person.name}</option>{/each}
												</select>
											</label>
										{/if}
										{#if product.personalization_enabled}
											<label class="grid gap-1 text-sm">{t('shop.orders.line.pers_name')}
												<input class="input input-bordered w-full" maxlength="100" value={draft.name} oninput={(e) => changeDraft(product, { name: e.currentTarget.value })} />
											</label>
											<label class="grid gap-1 text-sm">{t('shop.orders.line.pers_number')}
												<input class="input input-bordered w-full" maxlength="20" value={draft.number} oninput={(e) => changeDraft(product, { number: e.currentTarget.value })} />
											</label>
										{/if}
										<div class="flex flex-wrap gap-2">
											<input class="input input-bordered w-20" aria-label={t('portal.shop.quantity_for', { name: product.name })} type="number" min="1" max="999" step="1" value={draft.quantity} oninput={(e) => changeDraft(product, { quantity: e.currentTarget.valueAsNumber })} />
											<button class="btn btn-primary grow" type="button" disabled={busy || (product.variants.length > 0 && !variant?.in_stock) || !Number.isInteger(draft.quantity) || draft.quantity < 1 || draft.quantity > 999} onclick={() => add(product)}>
												{t('portal.shop.add')}
											</button>
										</div>
									{:else}
										<span class="badge badge-outline">{t('shop.out_of_stock')}</span>
									{/if}
								</div>
							</article>
						{:else}
							<p class="text-sm text-muted">{t('portal.shop.no_products')}</p>
						{/each}
					</div>
				</section>
			{/if}

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
		</div>

		{#if catalog.open || cart.length}
			<section class="card card-body scroll-mt-44 gap-3 xl:scroll-mt-20" id="cart" aria-live="polite">
				<h2 class="card-title"><ShoppingCart size={16} aria-hidden="true" /> {t('portal.shop.cart')}</h2>
				{#if cartError}<Alert kind="error">{cartError}</Alert>{/if}
				{#if cart.length}
					<form class="grid gap-3" onsubmit={checkout}>
						<ul class="divide-y divide-rule">
							{#each cart as line (line.id)}
								<li class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 py-2">
									<div>
										<p class="text-sm font-semibold">{label(line)}</p>
										{#if buyerName(line.beneficiary_id)}<p class="text-xs text-muted">{buyerName(line.beneficiary_id)}</p>{/if}
										{#if line.personalization_name || line.personalization_number}<p class="text-xs text-muted">{[line.personalization_name, line.personalization_number].filter(Boolean).join(' · ')}</p>{/if}
										{#if line.ordering_deadline && line.estimated_delivery}<p class="text-xs text-muted">{t('shop.window.summary', { deadline: calendarDay(line.ordering_deadline), estimated_delivery: calendarDay(line.estimated_delivery) })}</p>{/if}
										{#if catalog.open && !currentLine(line)}<p class="text-xs text-error">{t('portal.shop.line_unavailable')}</p>{/if}
									</div>
									<button class="btn btn-ghost btn-square btn-sm" type="button" disabled={busy} aria-label={t('portal.shop.remove', { name: label(line) })} onclick={() => updateCart(cart.filter((item) => item.id !== line.id))}><Trash2 size={16} aria-hidden="true" /></button>
									<div class="col-span-2 mt-2 flex items-center justify-between gap-2">
										<input class="input input-bordered input-sm w-20" type="number" min="1" max="999" step="1" required disabled={busy} aria-label={t('portal.shop.quantity_for', { name: label(line) })} value={line.quantity} onchange={(e) => {
											const quantity = e.currentTarget.valueAsNumber;
											if (Number.isInteger(quantity) && quantity >= 1 && quantity <= 999) updateCart(cart.map((item) => item.id === line.id ? { ...item, quantity } : item));
											else e.currentTarget.value = String(line.quantity);
										}} />
										<span class="text-sm font-semibold tabular-nums">{money(Number(line.unit_price) * line.quantity)}</span>
									</div>
								</li>
							{/each}
						</ul>
						{#if stale}
							<Alert kind="warning">{t('portal.shop.changed')}</Alert>
							<button class="btn btn-outline" type="button" onclick={() => updateCart(cart.map((item) => currentLine(item) ?? item))}>{t('portal.shop.update')}</button>
						{/if}
						{#if catalog.open}
							<label class="grid gap-1 text-sm">{t('shop.orders.new.discount_code')}
								<input class="input input-bordered w-full" disabled={busy} maxlength="255" value={discount} oninput={(e) => { discount = e.currentTarget.value; checkoutId = ''; }} />
							</label>
						{/if}
						<div class="flex justify-between gap-2 border-t border-line pt-2 text-sm font-semibold"><span>{t('portal.shop.estimate')}</span><span class="tabular-nums">{money(subtotal)}</span></div>
						<button class="btn btn-primary w-full" type="submit" disabled={busy || !restored || !catalog.open || stale || unavailable}>{t('portal.shop.place')}</button>
					</form>
				{:else}
					<p class="text-sm text-muted">{t('portal.shop.empty')}</p>
				{/if}
			</section>
		{/if}
	</div>
</div>
