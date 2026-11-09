<script lang="ts">
	import { untrack } from 'svelte';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { Layers, Pencil, Plus, Search, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	let { data, form } = $props();
	let addOpen = $state(false);
	let busy = $state(false);
	// svelte-ignore state_referenced_locally
	let q = $state(data.filters.q);
	$effect(() => {
		const external = data.filters.q;
		// react to the URL changing (clear, back), never to what is being typed
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});

	const href = (patch: Partial<{ q: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => q.trim() !== data.filters.q && goto(href({ q: q.trim() }), { replace: true, reset: false }), 400);
	}
	const filtered = $derived(!!data.filters.q);

	usePageHeader(() => ({ title: t('groups.title'), context: data.count === 1 ? t('groups.count_one') : t('groups.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	{#if data.me?.can_manage_members}<button class="btn btn-primary gap-2" type="button" onclick={() => (addOpen = true)}><Plus size={16} /> {t('groups.add')}</button>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('groups.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5" role="search">
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('groups.search')} placeholder={t('groups.search_placeholder')} class="input w-64 pl-9" bind:value={q} oninput={onType} />
		</div>
		{#if filtered}
			<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('common.clear_filters')}</a>
		{/if}
	</div>

	<div class="px-7 py-5">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Layers size={28} />
				<p>{filtered ? t('groups.empty_filtered') : t('groups.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('groups.col.group')}</th><th class="text-right">{t('groups.col.count')}</th><th></th></tr>
					</thead>
					<tbody>
						{#each data.rows as g (g.id)}
							<tr class="hover:bg-surface">
								<td class="font-semibold"><a class="link" href="/groups/{g.id}">{g.name}</a></td>
								<td class="text-right font-mono text-xs">{g.member_count}</td>
								<td class="text-right"><a class="btn btn-outline btn-xs" href="/groups/{g.id}" aria-label={t('groups.edit_named', { name: g.name })}><Pencil size={13} /> {t('common.edit')}</a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(offset) => href({ offset })} />
		{/if}
	</div>
</div>

<Modal bind:open={addOpen} title={t('groups.add')}>
	<form
		method="POST"
		action="?/create"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
			};
		}}
	>
		{#if form?.groupAction === 'create' && form.groupError && !form.groupFields?.name}<Alert kind="error" title={t('groups.create_failed')}>{form.groupError}</Alert>{/if}
		<Field label={t('groups.name')} name="name" required error={form?.groupFields?.name} autocomplete="off" />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Plus size={16} /> {t('groups.create')}</button>
		</div>
	</form>
</Modal>
