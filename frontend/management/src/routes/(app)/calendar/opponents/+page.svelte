<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { Pencil, Plus, Search, Swords, Trash2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import type { Opponent } from '#lib/calendar.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canManageCalendar } from '#lib/permissions.ts';

	let { data, form } = $props();
	const manage = $derived(canManageCalendar(data.me));

	let formOpen = $state(false);
	let editing = $state<Opponent | null>(null);
	let deleting = $state<Opponent | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);
	// svelte-ignore state_referenced_locally
	let q = $state(data.q);
	$effect(() => {
		if (data.q !== q.trim()) q = data.q;
	});
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => q.trim() !== data.q && goto(q.trim() ? `?q=${encodeURIComponent(q.trim())}` : '?', { replace: true, reset: false }), 400);
	}

	const mine = $derived(form?.oppAction === 'save');
	const fe = (f: string) => (mine ? (form?.oppFields?.[f] as string | undefined) : undefined);
	const formLevel = $derived(mine && form?.oppError && !Object.keys(form?.oppFields ?? {}).length ? (form.oppError as string) : '');
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	usePageHeader(() => ({ title: t('opponents.title'), context: data.opponents.length === 1 ? t('opponents.count_one') : t('opponents.count', { n: data.opponents.length }), actions: manage ? actions : null }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => ((editing = null), (formOpen = true))}><Plus size={16} /> {t('opponents.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('opponents.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5" role="search">
		<div class="relative w-[26rem] max-w-full">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('opponents.search')} placeholder={t('opponents.search_placeholder')} class="input w-full pl-9" bind:value={q} oninput={onType} />
		</div>
		{#if data.q}<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('members.list.clear')}</a>{/if}
	</div>

	<div class="px-7 py-5">
		{#if form?.oppAction === 'delete' && form.oppError}<Alert kind="error" title={t('opponents.delete_failed')}>{form.oppError}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.opponents.length}
			<div class="card card-body items-center py-10 text-center text-muted"><Swords size={28} /><p>{data.q ? t('opponents.empty_filtered') : t('opponents.empty')}</p></div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('opponents.col.name')}</th><th>{t('opponents.col.alternative')}</th><th class="text-right">{t('opponents.col.events')}</th><th></th></tr></thead>
					<tbody>
						{#each data.opponents as o (o.id)}
							<tr class="hover:bg-surface">
								<td>
									<div class="flex items-center gap-3"><ClubCrest name={o.name} logoUrl={o.logo_url} size={32} /><span class="font-semibold">{o.name}</span></div>
								</td>
								<td>
									<div class="flex flex-wrap gap-1">
										{#each o.alternative_names as a (a)}<span class="badge badge-sm badge-outline">{a}</span>{:else}<span class="text-sm text-muted">—</span>{/each}
									</div>
								</td>
								<td class="text-right font-mono text-xs">{o.event_count}</td>
								<td class="text-right whitespace-nowrap">
									{#if manage}
										<button class="btn btn-outline btn-xs gap-1" type="button" aria-label={t('opponents.edit_named', { name: o.name })} onclick={() => ((editing = o), (formOpen = true))}><Pencil size={13} /> {t('common.edit')}</button>
										<button class="btn btn-outline btn-error btn-xs gap-1" type="button" aria-label={t('opponents.delete_named', { name: o.name })} onclick={() => ((deleting = o), (deleteOpen = true))}><Trash2 size={13} /> {t('common.delete')}</button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={formOpen} title={editing ? t('opponents.edit') : t('opponents.add')}>
	<form method="POST" action="?/save" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if formLevel}<Alert kind="error">{formLevel}</Alert>{/if}
		<Field label={t('opponents.f.name')} name="name" value={editing?.name ?? ''} required error={fe('name')} autocomplete="off" />
		<div class="form-control w-full">
			<label class="label" for="opp-alt"><span class="label-text">{t('opponents.f.alternative')}</span></label>
			<textarea id="opp-alt" name="alternative_names" class="textarea textarea-bordered w-full {fe('alternative_names') ? 'textarea-error' : ''}" rows="3" value={(editing?.alternative_names ?? []).join('\n')}></textarea>
			<span class="mt-1 block text-xs {fe('alternative_names') ? 'text-error' : 'text-muted'}">{fe('alternative_names') ?? t('opponents.f.alternative_help')}</span>
		</div>
		<div class="form-control w-full">
			<label class="label" for="opp-logo"><span class="label-text">{t('opponents.f.logo')}</span></label>
			<div class="flex items-center gap-3">
				{#if editing}<ClubCrest name={editing.name} logoUrl={editing.logo_url} size={40} />{/if}
				<input id="opp-logo" name="logo" type="file" accept="image/jpeg,image/png,image/webp" class="file-input w-full" />
			</div>
			{#if fe('logo')}<span class="mt-1 block text-xs text-error">{fe('logo')}</span>{/if}
			{#if editing?.logo_url}<label class="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" name="remove_logo" class="checkbox checkbox-sm" /> {t('opponents.f.remove_logo')}</label>{/if}
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('opponents.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		<p>{t('opponents.delete_body', { name: deleting?.name ?? '' })}</p>
		{#if deleting?.event_count}<Alert kind="warning">{t('opponents.delete_in_use', { n: deleting.event_count })}</Alert>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
