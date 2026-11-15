<script lang="ts">
		import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FolderTree, Package, Pencil, Plus, Tags, Trash2, Lock } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import Field from '#lib/components/Field.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, money, productHref, type Category, type ProductItem } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; category: string; active: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.category) sp.set('category', f.category);
		if (f.active !== 'all') sp.set('active', f.active);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || !!data.filters.category || data.filters.active !== 'all');
	const filterTabs = $derived([
		{ key: 'all', label: t('shop.filter.all') },
		{ key: 'active', label: t('shop.filter.active') },
		{ key: 'inactive', label: t('shop.filter.inactive') }
	]);

	let busy = $state(false);
	let target = $state<ProductItem | null>(null);
	let delOpen = $state(false);
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const blocked = $derived(!!form && 'code' in form && ['has_orders', 'has_stock_history'].includes(String(form.code)) && form.op === 'delete');
	const failure = $derived(form && 'op' in form && !blocked && !('done' in form) && form.op !== 'categorySave' && form.op !== 'categoryDelete' ? String(form.message || t('common.error.generic')) : '');

	// -- categories --
	let catOpen = $state(false);
	let catDelOpen = $state(false);
	let cat = $state<Category | null>(null);
	let catName = $state('');
	const catErr = $derived(form && form.op === 'categorySave' && 'fields' in form ? ((form.fields as Record<string, string>).name ?? '') : '');
	const catFailure = $derived(form && (form.op === 'categoryDelete' || (form.op === 'categorySave' && !catErr)) && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	function askCat(c: Category | null) {
		cat = c;
		catName = c?.name ?? '';
		catOpen = true;
	}

	usePageHeader(() => ({ title: t('shop.product.title'), context: data.count === 1 ? t('shop.product.count_one') : t('shop.product.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-primary gap-2" href="/shop/products/new"><Plus size={16} /> {t('shop.product.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.product.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.product.search')} searchPlaceholder={t('shop.product.search_placeholder')} onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		<select class="select w-auto" aria-label={t('shop.product.f.category')} value={data.filters.category} onchange={(e) => goto(href({ category: e.currentTarget.value }), { replace: true, reset: false })}>
			<option value="">{t('shop.product.all_categories')}</option>
			{#each data.categories as c (c.id)}<option value={c.id}>{c.name}{c.is_system ? ` (${t('shop.cat.system_short')})` : ''}</option>{/each}
		</select>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.active} label={t('shop.filter.label')} hrefFor={(key) => href({ active: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid items-start gap-5 px-7 py-5 xl:grid-cols-3">
		<div class="grid gap-4 xl:col-span-2">
			{#if deleted || done === 'delete'}<Alert kind="success">{t('shop.product.deleted')}</Alert>{/if}
			{#if done === 'deactivate'}<Alert kind="success">{t('shop.product.deactivated')}</Alert>{/if}
			{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}
			{#if blocked && form && 'id' in form}
				{@const p = data.rows.find((r) => r.id === form.id)}
				<Alert kind="warning" title={t('shop.product.delete.blocked_title')}>
					<p>{t('shop.product.delete.blocked', { name: p?.name ?? '' })}</p>
					<form method="POST" action="?/deactivate" class="mt-2" use:enhance>
						<input type="hidden" name="id" value={form.id} />
						<button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button>
					</form>
				</Alert>
			{/if}

			{#if data.error}
				<Alert kind="error">{t('common.error.generic')}</Alert>
			{:else if !data.rows.length}
				<div class="card card-body items-center py-10 text-center text-muted">
					<Package size={28} />
					<p>{filtered ? t('shop.product.empty_filtered') : t('shop.product.empty')}</p>
				</div>
			{:else}
				<div class="card overflow-x-auto">
					<table class="table">
						<thead><tr><th>{t('shop.col.product')}</th><th>{t('shop.product.f.category')}</th><th class="text-right">{t('shop.col.price')}</th><th>{t('shop.col.stock')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
						<tbody>
							{#each data.rows as p (p.id)}
								<tr class="hover:bg-surface">
									<td>
										<div class="flex items-center gap-3">
											{#if p.image_url}<img src={p.image_url} alt="" class="size-10 rounded-md border border-line object-cover" />{:else}<span class="flex size-10 items-center justify-center rounded-md border border-line text-dim"><Package size={16} /></span>{/if}
											<div>
												<a class="link font-semibold" href={productHref(p.id)}>{p.name}</a>
												<div class="mt-0.5 flex flex-wrap items-center gap-2">
													{#if !p.is_active}<span class="badge badge-sm badge-warning">{t('shop.inactive')}</span>{/if}
													{#if !p.is_public}<span class="badge badge-sm">{t('shop.product.private')}</span>{/if}
													{#if p.variant_count}<span class="text-xs text-muted">{t('shop.product.variants_n', { n: p.variant_count })}</span>{/if}
												</div>
											</div>
										</div>
									</td>
									<td class="text-sm">{#if p.category?.system_key}<span class="inline-flex items-center gap-1" title={t('shop.cat.system_reason')}><Lock size={11} class="text-muted" aria-hidden="true" />{p.category.name}</span>{:else}{p.category?.name ?? t('common.none')}{/if}</td>
									<td class="text-right font-mono text-sm whitespace-nowrap">{money(p.price)}</td>
									<td>
										{#if p.inventory_managed || p.ordering_closed}<span class="badge badge-sm {p.in_stock ? 'badge-success' : 'badge-error'}">{p.in_stock ? t('shop.in_stock') : t('shop.out_of_stock')}</span>{:else if !p.next_ordering_window}<span class="text-xs text-muted">{t('shop.not_tracked')}</span>{/if}
										{#if p.next_ordering_window}<p class="mt-1 text-xs text-muted">{t('shop.window.summary', { deadline: day(p.next_ordering_window.deadline), estimated_delivery: day(p.next_ordering_window.estimated_delivery) })}</p>{/if}
									</td>
									<td class="text-right whitespace-nowrap">
										<div class="flex justify-end gap-1">
											<a class="btn btn-outline btn-xs gap-1" href={productHref(p.id)} aria-label={t('shop.product.edit_named', { name: p.name })}><Pencil size={13} /> {t('common.edit')}</a>
											<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('shop.product.delete_named', { name: p.name })} onclick={() => ((target = p), (delOpen = true))}><Trash2 size={13} /></button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
			{/if}
		</div>

		<aside class="card card-body gap-3 xl:sticky xl:top-20" aria-labelledby="cat-h">
			<div class="flex items-center justify-between gap-2">
				<h2 id="cat-h" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><FolderTree size={16} class="shrink-0" aria-hidden="true" /> {t('shop.cat.title')}</h2>
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => askCat(null)}><Plus size={14} /> {t('shop.cat.add')}</button>
			</div>
			{#if done === 'category_saved'}<Alert kind="success">{t('shop.cat.saved')}</Alert>{/if}
			{#if done === 'category_deleted'}<Alert kind="success">{t('shop.cat.deleted')}</Alert>{/if}
			{#if catFailure}<Alert kind="error">{catFailure}</Alert>{/if}
			{#if data.categories.length}
				<ul class="divide-y divide-rule">
					{#each data.categories as c (c.id)}
						<li class="flex items-center justify-between gap-2 py-2">
							<span class="flex min-w-0 items-center gap-2">
								<a class="link truncate text-sm font-semibold" href={href({ category: c.id })}>{c.name}</a>
								{#if c.is_system}<span class="badge badge-sm shrink-0 gap-1" title={t('shop.cat.system_reason')}><Lock size={10} aria-hidden="true" /> {t('shop.cat.system')}</span>{/if}
							</span>
							<span class="flex items-center gap-1">
								<span class="font-mono text-xs text-muted">{c.product_count}</span>
								<!-- The system categories keep both buttons, disabled, with the reason as a tooltip and for screen readers. -->
								<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('common.edit')} aria-describedby={c.is_system ? `sys-${c.id}` : undefined} disabled={c.is_system} title={c.is_system ? t('shop.cat.system_reason') : undefined} onclick={() => askCat(c)}><Pencil size={13} /></button>
								<button class="btn btn-ghost btn-xs btn-square {c.is_system ? '' : 'text-error'}" type="button" aria-label={t('common.delete')} aria-describedby={c.is_system ? `sys-${c.id}` : undefined} disabled={c.is_system} title={c.is_system ? t('shop.cat.system_reason') : undefined} onclick={() => ((cat = c), (catDelOpen = true))}><Trash2 size={13} /></button>
								{#if c.is_system}<span id="sys-{c.id}" class="sr-only">{t('shop.cat.system_reason')}</span>{/if}
							</span>
						</li>
					{/each}
				</ul>
				<p class="flex items-start gap-2 text-xs text-muted" title={t('shop.cat.system_reason')}><Lock size={12} class="mt-0.5 shrink-0" aria-hidden="true" /> {t('shop.cat.system_help')}</p>
			{:else}
				<p class="flex items-center gap-2 text-sm text-muted"><Tags size={16} /> {t('shop.cat.empty')}</p>
			{/if}
		</aside>
	</div>
</div>

<Modal bind:open={delOpen} title={t('shop.product.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; delOpen = false; }; }}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.product.delete.body', { name: target?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={catOpen} title={cat ? t('shop.cat.edit') : t('shop.cat.add')}>
	<form method="POST" action="?/categorySave" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type === 'success' && result.data && 'done' in result.data) catOpen = false; }; }}>
		<input type="hidden" name="id" value={cat?.id ?? ''} />
		<Field label={t('shop.cat.f.name')} name="name" bind:value={catName} error={catErr} maxlength={255} required />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (catOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy || !catName.trim()}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={catDelOpen} title={t('shop.cat.delete_title')}>
	<form method="POST" action="?/categoryDelete" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; catDelOpen = false; }; }}>
		<input type="hidden" name="id" value={cat?.id ?? ''} />
		<p>{t('shop.cat.delete_body', { name: cat?.name ?? '', n: cat?.product_count ?? 0 })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (catDelOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
