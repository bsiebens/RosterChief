<script lang="ts">
		import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Check, Copy, Download, Plus, Power, Ticket, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, money, statusBadge, voucherHref, VOUCHER_STATUSES, type Voucher } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; status: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.status && f.status !== 'usable') sp.set('status', f.status); // usable is the default tab
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.status !== 'usable');
	const filterTabs = $derived([...VOUCHER_STATUSES.map((s) => ({ key: s, label: t(`shop.status.${s}`) })), { key: 'all', label: t('shop.filter.all') }]);

	let busy = $state(false);
	let target = $state<Voucher | null>(null);
	let delOpen = $state(false);
	let copied = $state('');
	async function copy(v: Voucher) {
		try {
			await navigator.clipboard.writeText(v.number);
			copied = v.id;
			setTimeout(() => (copied = ''), 1500);
		} catch {
			/* clipboard unavailable */
		}
	}
	// A CSV of the vouchers on this page (the API has no export): number, holder, amount, available, expiry, status.
	function csv() {
		const esc = (s: string) => `"${s.replaceAll('"', '""')}"`;
		const head = ['number', 'issued_to', 'amount', 'consumed', 'available', 'expiry_date', 'status'];
		const lines = data.rows.map((v) => [v.number, v.issued_to?.name ?? '', v.amount, v.consumed_amount, v.available_amount, v.expiry_date, v.status].map(esc).join(','));
		const url = URL.createObjectURL(new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8' }));
		const a = document.createElement('a');
		a.href = url;
		a.download = 'vouchers.csv';
		a.click();
		URL.revokeObjectURL(url);
	}
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const blocked = $derived(!!form && 'code' in form && form.code === 'has_usage');
	const failure = $derived(form && 'op' in form && !blocked && !('done' in form) ? String(form.message || t('common.error.generic')) : '');

	usePageHeader(() => ({ title: t('shop.vch.title'), context: data.count === 1 ? t('shop.vch.count_one') : t('shop.vch.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	{#if data.rows.length}<button class="btn btn-outline gap-2" type="button" onclick={csv}><Download size={16} /> {t('shop.vch.csv')}</button>{/if}
	<a class="btn btn-primary gap-2" href="/shop/vouchers/new"><Plus size={16} /> {t('shop.vch.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.vch.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('shop.vch.search')} searchPlaceholder={t('shop.vch.search_placeholder')} onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => goto('?', { reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.status} label={t('shop.filter.label')} hrefFor={(key) => href({ status: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-7 py-5">
		{#if data.created}<Alert kind="success">{t('shop.vch.created_n', { n: data.created })}</Alert>{/if}
		{#if deleted || done === 'delete'}<Alert kind="success">{t('shop.vch.deleted')}</Alert>{/if}
		{#if done === 'deactivate'}<Alert kind="success">{t('shop.vch.deactivated')}</Alert>{/if}
		{#if done === 'activate'}<Alert kind="success">{t('shop.vch.activated')}</Alert>{/if}
		{#if failure}<Alert kind="error" title={t('shop.action_failed')}>{failure}</Alert>{/if}
		{#if blocked && form && 'id' in form}
			{@const v = data.rows.find((r) => r.id === form.id)}
			<Alert kind="warning" title={t('shop.vch.delete.blocked_title')}>
				<p>{t('shop.vch.delete.blocked', { number: v?.number ?? '' })}</p>
				<form method="POST" action="?/toggle" class="mt-2" use:enhance>
					<input type="hidden" name="id" value={form.id} />
					<button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button>
				</form>
			</Alert>
		{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted"><Ticket size={28} /><p>{filtered ? t('shop.vch.empty_filtered') : t('shop.vch.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('shop.vch.number')}</th><th>{t('shop.vch.holder')}</th><th class="text-right">{t('shop.vch.amount')}</th><th class="text-right">{t('shop.vch.remaining')}</th><th>{t('shop.vch.expiry')}</th><th>{t('shop.col.status')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
					<tbody>
						{#each data.rows as v (v.id)}
							<tr class="hover:bg-surface">
								<td class="whitespace-nowrap">
									<a class="link font-mono font-semibold" href={voucherHref(v.id)}>{v.number}</a>
									<button class="btn btn-ghost btn-xs btn-square ml-1" type="button" aria-label={t('shop.vch.copy_named', { number: v.number })} onclick={() => copy(v)}>{#if copied === v.id}<Check size={13} />{:else}<Copy size={13} />{/if}</button>
								</td>
								<td class="text-sm">{v.issued_to?.name ?? t('common.none')}</td>
								<td class="text-right font-mono text-sm">{money(v.amount)}</td>
								<td class="text-right font-mono text-sm font-bold">{money(v.available_amount)}</td>
								<td class="text-xs whitespace-nowrap text-muted">{day(v.expiry_date)}</td>
								<td><span class="badge badge-sm {statusBadge(v.status)}">{t(`shop.status.${v.status}`)}</span></td>
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<form method="POST" action="?/toggle" use:enhance>
											<input type="hidden" name="id" value={v.id} />
											<input type="hidden" name="activate" value={v.is_active ? '0' : '1'} />
											<button class="btn btn-outline btn-xs gap-1" type="submit"><Power size={13} /> {v.is_active ? t('shop.deactivate') : t('shop.activate')}</button>
										</form>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('shop.vch.delete_named', { number: v.number })} onclick={() => ((target = v), (delOpen = true))}><Trash2 size={13} /></button>
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

<Modal bind:open={delOpen} title={t('shop.vch.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; delOpen = false; }; }}>
		<input type="hidden" name="id" value={target?.id ?? ''} />
		<p>{t('shop.vch.delete.body', { number: target?.number ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
