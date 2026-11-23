<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ExternalLink, Handshake, Pencil, Plus, Trash2, Wallet } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { formatDay, isoDate } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { billingBadge, money } from '#lib/sponsor-billing.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	type Sponsor = (typeof data.sponsors)[number];
	const badge = { active: 'badge-success', upcoming: 'badge-info', ended: 'badge-neutral', suspended: 'badge-error' };
	let editing = $state<Sponsor | null>(null);
	let deleting = $state<Sponsor | null>(null);
	let editOpen = $state(false);
	let deleteOpen = $state(false);
	let busy = $state(false);
	let submitted = $state(false);
	let startDate = $state('');
	const failure = $derived(submitted && form && 'op' in form ? form : null);
	const fieldError = (key: string) => failure?.op === 'save' ? failure.fields[key] : undefined;

	function edit(sponsor: Sponsor | null) {
		editing = sponsor;
		startDate = sponsor?.start_date ?? isoDate(new Date());
		submitted = false;
		editOpen = true;
	}
	function remove(sponsor: Sponsor) {
		deleting = sponsor;
		submitted = false;
		deleteOpen = true;
	}
	const submit = (close: () => void): SubmitFunction => () => {
		busy = true;
		return async ({ update, result }) => {
			await update({ reset: false });
			busy = false;
			submitted = true;
			if (result.type === 'success') close();
		};
	};
	const external = (url: string) => /^https?:\/\//i.test(url);
	// Filters live in the URL, like the other finance lists; the list is short, so they apply here rather than on the server.
	const q = $derived(page.url.searchParams.get('q') ?? '');
	const status = $derived(page.url.searchParams.get('status') ?? 'all');
	const visible = $derived(data.sponsors.filter((s) => (status === 'all' || s.status === status) && (!q || `${s.name} ${s.url}`.toLowerCase().includes(q.toLowerCase()))));
	const count = (key: string) => data.sponsors.filter((s) => s.status === key).length;
	const tabList = $derived([
		{ key: 'all', label: t('fin.tab.all') },
		{ key: 'active', label: t('sponsors.active'), count: count('active') },
		{ key: 'upcoming', label: t('sponsors.upcoming'), count: count('upcoming') },
		{ key: 'ended', label: t('sponsors.ended'), count: count('ended') },
		...(count('suspended') ? [{ key: 'suspended', label: t('sponsors.suspended'), count: count('suspended'), warn: true }] : [])
	]);
	const href = (patch: Record<string, string>) => {
		const sp = new URLSearchParams(page.url.search);
		for (const [k, v] of Object.entries(patch)) (v && !(k === 'status' && v === 'all') ? sp.set(k, v) : sp.delete(k));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string, replace = false) => goto(to, { reset: false, replaceState: replace });
	usePageHeader(() => ({ title: t('sponsors.title'), context: String(data.sponsors.length), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => edit(null)}><Plus size={16} aria-hidden="true" /> {t('sponsors.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('sponsors.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={q} searchLabel={t('fin.search')} searchPlaceholder={t('sponsors.search')} onsearch={(v) => navigate(href({ q: v }), true)} showClear={!!q} onclear={() => navigate(href({ q: '' }))}>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={status} label={t('sponsors.status')} hrefFor={(key) => href({ status: key })} onnavigate={(to) => navigate(to)} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if data.loadFailed}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !visible.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Handshake size={28} aria-hidden="true" />
				<p>{data.sponsors.length ? t('sponsors.empty_filtered') : t('sponsors.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('sponsors.name')}</th><th>{t('sponsors.status')}</th><th>{t('spb.col.payments')}</th><th class="hidden lg:table-cell">{t('sponsors.start')}</th><th class="hidden lg:table-cell">{t('sponsors.end')}</th><th><span class="sr-only">{t('sponsors.actions')}</span></th></tr></thead>
					<tbody>
						{#each visible as sponsor (sponsor.id)}
							<tr class="hover:bg-paper">
								<td>
									<div class="flex min-w-36 items-center gap-3">
										{#if sponsor.logo_url}<img class="size-10 shrink-0 rounded object-contain" src={sponsor.logo_url} alt="" width="40" height="40" loading="lazy" />{/if}
										<div class="min-w-0">
											<div class="font-semibold text-ink">{sponsor.name}</div>
											{#if external(sponsor.url)}
												<a class="link link-hover inline-flex items-center gap-1 text-xs break-all" href={sponsor.url} target="_blank" rel="noopener noreferrer">{sponsor.url} <ExternalLink size={12} aria-hidden="true" /></a>
											{/if}
										</div>
									</div>
								</td>
								<td><span class="badge badge-sm {badge[sponsor.status]}">{t(`sponsors.${sponsor.status}`)}</span></td>
								<td>
									<a class="inline-flex flex-col items-start gap-0.5" href="/finance/sponsors/{sponsor.id}" aria-label={t('spb.manage') + ': ' + sponsor.name}>
										<span class="badge badge-sm {billingBadge(sponsor.billing.status)}">{t(`spb.status.${sponsor.billing.status}`, { n: sponsor.billing.overdue_days })}</span>
										{#if sponsor.billing.status !== 'none' && Number(sponsor.billing.open_amount) > 0}<span class="font-mono text-xs text-muted">{money(sponsor.billing.open_amount)}</span>{/if}
									</a>
								</td>
								<td class="hidden font-mono text-xs whitespace-nowrap lg:table-cell">{formatDay(sponsor.start_date)}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap lg:table-cell">{sponsor.end_date ? formatDay(sponsor.end_date) : '—'}</td>
								<td>
									<div class="flex items-center justify-end gap-1">
										<a class="btn btn-outline btn-xs btn-square" href="/finance/sponsors/{sponsor.id}" aria-label={t('spb.manage') + ': ' + sponsor.name} title={t('spb.manage')}><Wallet size={13} /></a>
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('sponsors.edit_named', { name: sponsor.name })} onclick={() => edit(sponsor)} title={t('common.edit')}><Pencil size={13} /></button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('sponsors.delete_named', { name: sponsor.name })} onclick={() => remove(sponsor)} title={t('common.delete')}><Trash2 size={13} /></button>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={editOpen} title={editing ? t('sponsors.edit') : t('sponsors.add')}>
	<form method="POST" action="?/save" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit(() => (editOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if failure?.op === 'save' && !Object.keys(failure.fields).length}<Alert kind="error">{failure.message || t('common.error.generic')}</Alert>{/if}
		<Field name="name" label={t('sponsors.name')} value={editing?.name ?? ''} error={fieldError('name')} required maxlength={255} />
		<Field name="url" type="url" label={t('sponsors.website')} value={editing?.url ?? ''} error={fieldError('url')} maxlength={200} />
		<div class="grid gap-3 sm:grid-cols-2">
			<Field name="start_date" type="date" label={t('sponsors.start')} bind:value={startDate} error={fieldError('start_date')} required />
			<Field name="end_date" type="date" label={t('sponsors.end')} value={editing?.end_date ?? ''} min={startDate} error={fieldError('end_date')} />
		</div>
		<div class="form-control">
			<label class="label" for="sponsor-logo"><span class="label-text">{t('sponsors.logo')}</span></label>
			<div class="flex items-center gap-3">
				{#if editing?.logo_url}<img src={editing.logo_url} alt="" width="48" height="48" class="size-12 shrink-0 rounded object-contain" />{/if}
				<input class="file-input w-full" id="sponsor-logo" name="logo" type="file" accept="image/png,image/jpeg,image/webp" aria-describedby="sponsor-logo-help" aria-invalid={fieldError('logo') ? 'true' : undefined} />
			</div>
			<span id="sponsor-logo-help" class="mt-1 text-xs {fieldError('logo') ? 'text-error' : 'text-muted'}">{fieldError('logo') || t('sponsors.logo_help')}</span>
			{#if editing?.logo_url}<label class="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" name="remove_logo" class="checkbox checkbox-sm" /> {t('sponsors.remove_logo')}</label>{/if}
		</div>
		<div class="modal-action">
			<button class="btn btn-outline" type="button" disabled={busy} onclick={() => (editOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('sponsors.delete')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		<p>{t('sponsors.delete_body', { name: deleting?.name ?? '' })}</p>
		{#if failure?.op === 'delete'}<Alert kind="error">{failure.message || t('common.error.generic')}</Alert>{/if}
		<div class="modal-action">
			<button class="btn btn-outline" type="button" disabled={busy} onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} aria-hidden="true" /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
