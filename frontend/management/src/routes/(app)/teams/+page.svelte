<script lang="ts">
	import { untrack } from 'svelte';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { goto } from '$app/navigation';
	import { enhance } from '$app/forms';
	import { ArrowDown, ArrowUp, ChevronRight, GripVertical, Plus, Search, Shirt, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import TeamFormModal from '#lib/components/teams/TeamFormModal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canManageSettings, canManageTeam } from '#lib/permissions.ts';

	let { data, form } = $props();
	let addOpen = $state(false);
	let editing = $state<(typeof data.rows)[number] | null>(null);
	let editOpen = $state(false);
	const editAction = $derived(`?/save&id=${editing?.id ?? ''}${data.filters?.q ? `&q=${encodeURIComponent(data.filters.q)}` : ''}${data.filters?.season ? `&season=${data.filters.season}` : ''}${data.filters?.coach ? `&coach=${data.filters.coach}` : ''}`);

	// Order: drag the rows or use the arrows. Only while the list is unfiltered, so the saved order covers every team.
	let list = $state<typeof data.rows>([]);
	$effect(() => {
		list = [...(data.rows ?? [])];
	});
	const isSettings = $derived(canManageSettings(data.me));
	// Ordering a filtered subset would scramble the rest, so dragging only works on the full list.
	const canSort = $derived(!data.filters?.q && !data.filters?.coach && isSettings);
	let reorderForm: HTMLFormElement | undefined = $state();
	let idsValue = $state('');
	let dragId = $state<string | null>(null);
	let overId = $state<string | null>(null);
	const move = async (from: number, to: number) => {
		if (!canSort || from === to || to < 0 || to >= list.length) return;
		const next = [...list];
		next.splice(to, 0, next.splice(from, 1)[0]);
		list = next;
		idsValue = next.map((r) => r.id).join(',');
		await Promise.resolve();
		reorderForm?.requestSubmit();
	};
	const onDrop = (targetId: string) => {
		if (dragId && dragId !== targetId) move(list.findIndex((r) => r.id === dragId), list.findIndex((r) => r.id === targetId));
		dragId = overId = null;
	};
	// svelte-ignore state_referenced_locally
	let q = $state(data.filters.q);
	$effect(() => {
		const external = data.filters.q;
		// react to the URL changing (clear, back), never to what is being typed
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});

	const href = (patch: Partial<{ q: string; season: string; coach: string }>) => {
		const f = { ...data.filters, ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.season) sp.set('season', f.season);
		if (f.coach) sp.set('coach', f.coach);
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => q.trim() !== data.filters.q && goto(href({ q: q.trim() }), { replace: true, reset: false }), 400);
	}
	const filtered = $derived(!!data.filters.q || !!data.filters.season || !!data.filters.coach);
	const age = (min: number | null, max: number | null) => (min === null && max === null ? '—' : min !== null && max !== null ? `${min}–${max}` : min !== null ? `${min}+` : `≤ ${max}`);

	usePageHeader(() => ({ title: t('teams.title'), context: data.rows.length === 1 ? t('teams.count_one') : t('teams.count', { n: data.rows.length }), actions: isSettings ? actions : null }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => (addOpen = true)}><Plus size={16} /> {t('teams.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('teams.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-6 py-2.5" role="search">
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('teams.search')} placeholder={t('teams.search_placeholder')} class="input w-56 pl-9" bind:value={q} oninput={onType} />
		</div>
		<select class="select w-auto" aria-label={t('teams.season')} value={data.filters.season} onchange={(e) => goto(href({ season: e.currentTarget.value }), { reset: false })}>
			<option value="">{t('members.list.season_current')}</option>
			{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
		</select>
		<select class="select w-auto" aria-label={t('teams.coach')} value={data.filters.coach} onchange={(e) => goto(href({ coach: e.currentTarget.value }), { reset: false })}>
			<option value="">{t('teams.coach_any')}</option>
			<option value="none">{t('teams.coach_none')}</option>
		</select>
		{#if filtered}
			<a class="btn btn-outline gap-2" href="?"><X size={14} /> {t('common.clear_filters')}</a>
		{/if}
	</div>

	<div class="px-6 py-4">
		{#if form?.teamAction === 'reorder'}<Alert kind="error" title={t('teams.reorder_failed')}>{form.teamError}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Shirt size={28} />
				<p>{filtered ? t('teams.empty_filtered') : t('teams.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th class="w-24"><span class="sr-only">{t('teams.col.move')}</span></th><th>{t('teams.col.name')}</th><th class="hidden lg:table-cell">{t('teams.col.short')}</th><th>{t('teams.col.age')}</th><th class="hidden xl:table-cell">{t('teams.col.pool')}</th>
							<th class="text-right">{t('teams.col.players')}</th><th class="text-right">{t('teams.col.staff')}</th><th class="hidden xl:table-cell">{t('teams.col.feeds')}</th><th></th>
						</tr>
					</thead>
					<tbody>
						{#each list as r, i (r.id)}
							<tr
								class="hover:bg-paper {overId === r.id && dragId !== r.id ? 'outline outline-2 -outline-offset-2 outline-club' : ''} {dragId === r.id ? 'opacity-50' : ''}"
								draggable={canSort}
								ondragstart={(e) => {
									dragId = r.id;
									e.dataTransfer?.setData('text/plain', r.id);
									if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
								}}
								ondragover={(e) => {
									if (!canSort) return;
									e.preventDefault();
									overId = r.id;
								}}
								ondrop={(e) => {
									e.preventDefault();
									onDrop(r.id);
								}}
								ondragend={() => (dragId = overId = null)}>
								<td class="whitespace-nowrap">
									{#if canSort}
										<span class="inline-flex items-center gap-0.5 text-muted">
											<GripVertical size={16} class="cursor-grab" aria-hidden="true" />
											<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0} aria-label={t('teams.move_up', { name: r.name })} onclick={() => move(i, i - 1)}><ArrowUp size={14} /></button>
											<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === list.length - 1} aria-label={t('teams.move_down', { name: r.name })} onclick={() => move(i, i + 1)}><ArrowDown size={14} /></button>
										</span>
									{/if}
								</td>
								<td class="font-semibold">
									<a class="link" href="/teams/{r.id}{data.filters.season ? `?season=${data.filters.season}` : ''}">{r.name}</a>
									{#if r.tournament_only}<span class="badge badge-ghost badge-sm ml-2">{t('teams.tournament_only')}</span>{/if}
								</td>
								<td class="hidden text-sm text-muted lg:table-cell">{r.short_name || '—'}</td>
								<td class="font-mono text-xs">{age(r.age_min, r.age_max)}</td>
								<td class="hidden text-sm xl:table-cell">{r.pool_name ?? '—'}</td>
								<td class="text-right font-mono text-xs">{r.player_count}</td>
								<td class="text-right font-mono text-xs">{r.staff_count}</td>
								<td class="hidden text-sm xl:table-cell">{r.feeds_into_name ?? '—'}</td>
								<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1">
									{#if canManageTeam(data.me, r.id)}
									<button
										class="btn btn-ghost btn-xs btn-square text-muted"
										type="button"
										aria-label={t('teams.edit_named', { name: r.name })}
										onclick={() => {
											editing = r;
											editOpen = true;
										}}><ChevronRight size={16} /></button>
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

<form method="POST" action="?/reorder" bind:this={reorderForm} use:enhance={() => async ({ update }) => await update({ reset: false })}>
	<input type="hidden" name="ids" value={idsValue} />
</form>

<TeamFormModal bind:open={addOpen} title={t('teams.add')} pools={data.pools} teams={data.rows} action="?/create" name="create" {form} submitLabel={t('teams.create')} />

{#key editing?.id}
	<TeamFormModal bind:open={editOpen} title={t('teams.edit')} team={editing} pools={data.pools} teams={data.rows} action={editAction} name="save" {form} submitLabel={t('common.save')} lockStructure={!canManageSettings(data.me)} />
{/key}
