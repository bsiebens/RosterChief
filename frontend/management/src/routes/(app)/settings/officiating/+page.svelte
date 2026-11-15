<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ChevronRight, Pencil, Plus, Trash2, Whistle } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Level } from './+page.server.ts';

	let { data, form } = $props();

	let formOpen = $state(false);
	let editing = $state<Level | null>(null);
	let name = $state('');
	let deleting = $state<Level | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);

	const tabs = $derived([
		{ key: 'referee', label: t('levels.tab.referee') },
		{ key: 'official', label: t('levels.tab.official') }
	]);
	const levelName = (id: string | null) => data.levels.find((l) => l.id === id)?.name ?? '';
	// Teams a level only gets through the levels it inherits from (nearest ancestor first), with the level they come from.
	const inheritedTeams = (l: (typeof data.levels)[number]) => {
		const own = new Set(l.team_ids);
		const out: { id: string; from: string }[] = [];
		const seen = new Set([l.id]);
		let cur = data.levels.find((x) => x.id === l.inherits_from_id);
		while (cur && !seen.has(cur.id)) {
			seen.add(cur.id);
			for (const id of cur.team_ids) if (!own.has(id)) (own.add(id), out.push({ id, from: cur.name }));
			cur = data.levels.find((x) => x.id === cur!.inherits_from_id);
		}
		return out;
	};
	const teamName = (id: string) => data.teams.find((x) => x.id === id)?.name ?? '?';
	const parents = $derived(data.levels.filter((l) => l.id !== editing?.id));

	function openForm(level: Level | null) {
		editing = level;
		name = level?.name ?? '';
		formOpen = true;
	}
	function openDelete(level: Level) {
		deleting = level;
		deleteOpen = true;
	}
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};

	usePageHeader(() => ({ title: t('levels.title'), context: data.levels.length === 1 ? t('levels.count_one') : t('levels.count', { n: data.levels.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('levels.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('levels.title'))}</title></svelte:head>

<!-- Full-bleed like the members list: a filter-bar strip with the toggle on the right, content below. -->
<div class="-mx-7 -my-6 flex flex-col">
<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-6 py-2.5">
	<div class="flex-1"></div>
	<Tabs {tabs} active={data.tab} label={t('levels.tabs')} />
	<InfoTip label={t('levels.info_label')} text={t('levels.info')} />
</div>

<div class="grid gap-4 px-6 py-4">

	{#if data.error}
		<Alert kind="error">{t('levels.load_failed')}</Alert>
	{:else if !data.levels.length}
		<div class="card card-body items-center py-10 text-center text-muted">
			<Whistle size={28} />
			<p>{t('levels.empty')}</p>
		</div>
	{:else}
		<p class="flex flex-wrap items-center gap-2 text-xs text-muted"><span class="badge badge-sm badge-outline">{t('levels.legend_own')}</span><span class="badge badge-sm border-dashed border-club bg-club/10 text-club-dark">{t('levels.legend_inherited')}</span></p>
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr><th>{t('levels.col.name')}</th><th class="hidden md:table-cell">{t('levels.col.inherits')}</th><th class="hidden lg:table-cell">{t('levels.col.teams')}</th><th class="text-right">{t('levels.col.members')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each data.levels as l (l.id)}
						<tr class="hover:bg-paper">
							<td><button type="button" class="link text-left font-semibold" onclick={() => openForm(l)}>{l.name}</button></td>
							<td class="hidden md:table-cell">{levelName(l.inherits_from_id) || t('common.none')}</td>
							<td class="hidden lg:table-cell">
								<div class="flex flex-wrap gap-1">
									{#each l.team_ids as id (id)}<span class="badge badge-sm badge-outline">{teamName(id)}</span>{/each}
									{#each inheritedTeams(l) as it (it.id)}<span class="badge badge-sm border-dashed border-club bg-club/10 text-club-dark" title={t('levels.inherited_from', { level: it.from })}>{teamName(it.id)}</span>{/each}
									{#if !l.team_ids.length && !inheritedTeams(l).length}<span class="text-sm text-muted">—</span>{/if}
								</div>
							</td>
							<td class="text-right font-mono text-xs">{l.member_count}</td>
							<td class="text-right whitespace-nowrap">
								<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('levels.edit_named', { name: l.name })} onclick={() => openForm(l)}><ChevronRight size={16} /></button>
								<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('levels.delete_named', { name: l.name })} onclick={() => openDelete(l)}><Trash2 size={14} /></button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
</div>

<Modal bind:open={formOpen} title={editing ? t('levels.edit') : t('levels.add')}>
	<form method="POST" action="?/save&tab={data.tab}" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="kind" value={data.tab} />
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if form?.levelAction === 'save' && form.levelError && !Object.keys(form.levelFields ?? {}).length}<Alert kind="error" title={t('levels.save_failed')}>{form.levelError}</Alert>{/if}
		<Field label={t('levels.name')} name="name" required bind:value={name} error={form?.levelAction === 'save' ? form.levelFields?.name : undefined} autocomplete="off" />
		<div class="form-control w-full">
			<label class="label" for="inherits_from_id"><span class="label-text">{t('levels.inherits')}</span></label>
			<select id="inherits_from_id" name="inherits_from_id" class="select select-bordered w-full">
				<option value="">{t('levels.inherits_none')}</option>
				{#each parents as p (p.id)}<option value={p.id} selected={p.id === editing?.inherits_from_id}>{p.name}</option>{/each}
			</select>
			{#if form?.levelAction === 'save' && form.levelFields?.inherits_from_id}<span class="mt-1 block text-xs text-error">{form.levelFields.inherits_from_id}</span>{/if}
		</div>
		<fieldset class="form-control w-full">
			<legend class="label"><span class="label-text">{t('levels.teams')}</span></legend>
			{#if data.teams.length}
				<div class="max-h-56 overflow-y-auto rounded-lg border border-line p-2">
					{#each data.teams as team (team.id)}
						<label class="label cursor-pointer justify-start gap-2 py-1">
							<input type="checkbox" name="team_ids" value={team.id} class="checkbox checkbox-sm" checked={editing?.team_ids.includes(team.id) ?? false} />
							<span class="label-text">{team.name}</span>
						</label>
					{/each}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('levels.teams_empty')}</p>
			{/if}
			{#if form?.levelAction === 'save' && form.levelFields?.team_ids}<span class="mt-1 block text-xs text-error">{form.levelFields.team_ids}</span>{/if}
		</fieldset>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('levels.delete_title')}>
	<form method="POST" action="?/delete&tab={data.tab}" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="kind" value={data.tab} />
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		{#if form?.levelAction === 'delete' && form.levelError}<Alert kind="error" title={t('levels.delete_failed')}>{form.levelError}</Alert>{/if}
		<p>{t('levels.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
