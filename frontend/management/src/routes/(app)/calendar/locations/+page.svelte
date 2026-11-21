<script lang="ts">
	import { untrack } from 'svelte';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { MapPin, Pencil, Plus, Search, Trash2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import type { Location } from '#lib/calendar.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canManageCalendar } from '#lib/permissions.ts';

	let { data, form } = $props();
	const manage = $derived(canManageCalendar(data.me));

	let formOpen = $state(false);
	let editing = $state<Location | null>(null);
	let deleting = $state<Location | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);
	// svelte-ignore state_referenced_locally
	let q = $state(data.q);
	$effect(() => {
		const external = data.q;
		// react to the URL changing (clear, back), never to what is being typed
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => q.trim() !== data.q && goto(q.trim() ? `?q=${encodeURIComponent(q.trim())}` : '?', { replace: true, reset: false }), 400);
	}

	const mine = $derived(form?.locAction === 'save');
	const fe = (f: string) => (mine ? (form?.locFields?.[f] as string | undefined) : undefined);
	const formLevel = $derived(mine && form?.locError && !Object.keys(form?.locFields ?? {}).length ? (form.locError as string) : '');
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	usePageHeader(() => ({ title: t('locations.title'), context: data.locations.length === 1 ? t('locations.count_one') : t('locations.count', { n: data.locations.length }), actions: manage ? actions : null }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => ((editing = null), (formOpen = true))}><Plus size={16} /> {t('locations.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('locations.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5" role="search">
		<div class="relative w-[26rem] max-w-full">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('locations.search')} placeholder={t('locations.search_placeholder')} class="input w-full pl-9" bind:value={q} oninput={onType} />
		</div>
		{#if data.q}<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('common.clear_filters')}</a>{/if}
	</div>

	<div class="px-7 py-5">
		{#if form?.locAction === 'delete' && form.locError}<Alert kind="error" title={t('locations.delete_failed')}>{form.locError}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.locations.length}
			<div class="card card-body items-center py-10 text-center text-muted"><MapPin size={28} /><p>{data.q ? t('locations.empty_filtered') : t('locations.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('locations.col.name')}</th><th>{t('locations.col.address')}</th><th>{t('locations.col.country')}</th><th class="text-right">{t('locations.col.events')}</th><th></th></tr></thead>
					<tbody>
						{#each data.locations as l (l.id)}
							<tr class="hover:bg-surface">
								<td class="font-semibold">{l.name}{#if l.is_home} <span class="badge badge-sm badge-success ml-2">{t('locations.home')}</span>{/if}</td>
								<td class="text-sm">{l.address}, {l.zip_code} {l.city}{#if l.latitude !== null}<span class="block font-mono text-xs text-muted">{l.latitude.toFixed(4)}, {l.longitude?.toFixed(4)}</span>{/if}</td>
								<td class="text-sm text-muted">{l.country_name}</td>
								<td class="text-right font-mono text-xs">{l.event_count}</td>
								<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1">
									{#if manage}
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('locations.edit_named', { name: l.name })} onclick={() => ((editing = l), (formOpen = true))} title={t('common.edit')}><Pencil size={13} /></button>
										{#if !l.is_home}<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('locations.delete_named', { name: l.name })} onclick={() => ((deleting = l), (deleteOpen = true))} title={t('common.delete')}><Trash2 size={13} /></button>{/if}
									{/if}
								</div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={formOpen} title={editing ? t('locations.edit') : t('locations.add')}>
	<form method="POST" action="?/save" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if formLevel}<Alert kind="error">{formLevel}</Alert>{/if}
		<Field label={t('locations.f.name')} name="name" value={editing?.name ?? ''} required error={fe('name')} autocomplete="off" />
		<Field label={t('locations.f.address')} name="address" value={editing?.address ?? ''} required error={fe('address')} autocomplete="off" />
		<div class="grid grid-cols-[1fr_2fr] gap-3">
			<Field label={t('locations.f.zip')} name="zip_code" value={editing?.zip_code ?? ''} required error={fe('zip_code')} autocomplete="off" />
			<Field label={t('locations.f.city')} name="city" value={editing?.city ?? ''} required error={fe('city')} autocomplete="off" />
		</div>
		<div class="form-control w-full">
			<label class="label" for="loc-country"><span class="label-text">{t('locations.f.country')}</span></label>
			<select id="loc-country" name="country" class="select select-bordered w-full {fe('country') ? 'select-error' : ''}" required>
				<option value="" disabled selected={!editing?.country}>—</option>
				{#each data.countries as c (c.code)}<option value={c.code} selected={c.code === (editing?.country ?? 'BE')}>{c.name}</option>{/each}
			</select>
			{#if fe('country')}<span class="mt-1 block text-xs text-error">{fe('country')}</span>{/if}
		</div>
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('locations.f.latitude')} name="latitude" value={editing?.latitude === null || editing?.latitude === undefined ? '' : String(editing.latitude)} error={fe('latitude')} autocomplete="off" />
			<Field label={t('locations.f.longitude')} name="longitude" value={editing?.longitude === null || editing?.longitude === undefined ? '' : String(editing.longitude)} error={fe('longitude')} autocomplete="off" />
		</div>
		<p class="text-xs text-muted">{t('locations.f.coords_help')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('locations.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		<p>{t('locations.delete_body', { name: deleting?.name ?? '' })}</p>
		{#if deleting?.event_count}<Alert kind="warning">{t('locations.delete_in_use', { n: deleting.event_count })}</Alert>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
