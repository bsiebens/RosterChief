<script lang="ts">
		import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Pencil, Percent, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import DiscountValue from '#lib/components/shop/DiscountValue.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, discountHref, DISCOUNT_STATUSES, statusBadge, type Discount } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; status: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.status) sp.set('status', f.status);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || !!data.filters.status);
	const filterTabs = $derived([{ key: '', label: t('shop.filter.all') }, ...DISCOUNT_STATUSES.map((s) => ({ key: s, label: t(`shop.status.${s}`) }))]);

	let busy = $state(false);
	let target = $state<Discount | null>(null);
	let delOpen = $state(false);
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const blocked = $derived(!!form && 'code' in form && form.code === 'has_orders');
	const failure = $derived(form && 'op' in form && !blocked && !('done' in form) ? String(form.message || t('common.error.generic')) : '');

	usePageHeader(() => ({ title: t('shop.disc.title'), context: data.count === 1 ? t('shop.disc.count_one') : t('shop.disc.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-primary gap-2" href="/shop/discounts/new"><Plus size={16} /> {t('shop.disc.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.disc.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.disc.search')} searchPlaceholder={t('shop.disc.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.status} label={t('shop.filter.label')} hrefFor={(key) => href({ status: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		{#if deleted || done === 'delete'}<Alert kind="success">{t('shop.disc.deleted')}</Alert>{/if}
		{#if done === 'deactivate'}<Alert kind="success">{t('shop.disc.deactivated')}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}
		{#if blocked && form && 'id' in form}
			{@const d = data.rows.find((r) => r.id === form.id)}
			<Alert kind="warning" title={t('shop.disc.delete.blocked_title')}>
				<p>{t('shop.disc.delete.blocked', { name: d?.name ?? '' })}</p>
				<form method="POST" action="?/deactivate" class="mt-2" use:enhance>
					<input type="hidden" name="id" value={form.id} />
					<button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button>
				</form>
			</Alert>
		{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted"><Percent size={28} /><p>{filtered ? t('shop.disc.empty_filtered') : t('shop.disc.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('shop.disc.f.name')}</th><th>{t('shop.disc.f.code')}</th><th class="text-right">{t('shop.col.value')}</th><th>{t('shop.col.valid')}</th><th class="text-right">{t('shop.col.used')}</th><th>{t('shop.col.status')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
					<tbody>
						{#each data.rows as d (d.id)}
							<tr class="hover:bg-surface">
								<td><a class="link font-semibold" href={discountHref(d.id)}>{d.name}</a></td>
								<td class="font-mono text-sm">{d.code}</td>
								<td class="text-right text-sm"><DiscountValue {d} /></td>
								<td class="text-xs whitespace-nowrap text-muted">{#if d.valid_from || d.valid_until}{d.valid_from ? day(d.valid_from) : '…'} – {d.valid_until ? day(d.valid_until) : '…'}{:else}{t('shop.disc.always')}{/if}</td>
								<td class="text-right font-mono text-sm">{d.used_count}{d.max_uses ? ` / ${d.max_uses}` : ''}</td>
								<td><span class="badge badge-sm {statusBadge(d.status)}">{t(`shop.status.${d.status}`)}</span></td>
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<a class="btn btn-outline btn-xs gap-1" href={discountHref(d.id)} aria-label={t('shop.disc.edit_named', { name: d.name })}><Pencil size={13} /> {t('common.edit')}</a>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('shop.disc.delete_named', { name: d.name })} onclick={() => ((target = d), (delOpen = true))}><Trash2 size={13} /></button>
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
</div>

<Modal bind:open={delOpen} title={t('shop.disc.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; delOpen = false; }; }}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.disc.delete.body', { name: target?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
