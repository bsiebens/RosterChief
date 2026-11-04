<script lang="ts">
	import { enhance } from '$app/forms';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Pool, TeamRow } from '#lib/teams/server.ts';

	/** Create / edit form for a team. `action` is the full form action (e.g. `?/create`); `name` the page-level action name used to pick errors out of `form`. */
	let {
		open = $bindable(false),
		title,
		team = null,
		pools,
		teams,
		action,
		name,
		form,
		submitLabel,
		lockStructure = false
	}: { open: boolean; title: string; team?: TeamRow | null; pools: Pool[]; teams: { id: string; name: string }[]; action: string; name: string; form: any; submitLabel: string; lockStructure?: boolean } = $props();

	let busy = $state(false);
	const mine = $derived(form?.teamAction === name);
	const fields = $derived<Record<string, string>>(mine ? (form?.teamFields ?? {}) : {});
	const formLevel = $derived(mine && form?.teamError && !Object.keys(fields).length ? (form.teamError as string) : '');
	const num = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v));
</script>

<Modal bind:open {title}>
	<form
		method="POST"
		{action}
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'success' || result.type === 'redirect') open = false;
			};
		}}
	>
		{#if formLevel}<Alert kind="error">{formLevel}</Alert>{/if}
		<div class="grid grid-cols-[2fr_1fr] gap-3">
			<Field label={t('teams.f.name')} name="name" value={team?.name ?? ''} required error={fields.name} autocomplete="off" />
			<Field label={t('teams.f.short_name')} name="short_name" value={team?.short_name ?? ''} error={fields.short_name} autocomplete="off" />
		</div>
		<div class="form-control w-full">
			<label class="label" for="{name}-pool"><span class="label-text">{t('teams.f.pool')}</span></label>
			<select id="{name}-pool" name="pool_id" class="select select-bordered w-full" disabled={lockStructure}>
				<option value="">{t('teams.f.pool_none')}</option>
				{#each pools as p (p.id)}<option value={p.id} selected={p.id === team?.pool_id}>{p.name} ({p.min_number}–{p.max_number})</option>{/each}
			</select>
			{#if fields.pool_id}<span class="mt-1 block text-xs text-error">{fields.pool_id}</span>{/if}
		</div>
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('teams.f.age_min')} name="age_min" type="number" min="0" max="99" value={num(team?.age_min)} error={fields.age_min} disabled={lockStructure} />
			<Field label={t('teams.f.age_max')} name="age_max" type="number" min="0" max="99" value={num(team?.age_max)} error={fields.age_max} disabled={lockStructure} />
		</div>
		<div class="form-control w-full">
			<label class="label" for="{name}-feeds"><span class="label-text">{t('teams.f.feeds_into')}</span></label>
			<select id="{name}-feeds" name="feeds_into_id" class="select select-bordered w-full" disabled={lockStructure}>
				<option value="">{t('teams.f.feeds_none')}</option>
				{#each teams.filter((x) => x.id !== team?.id) as x (x.id)}<option value={x.id} selected={x.id === team?.feeds_into_id}>{x.name}</option>{/each}
			</select>
			{#if fields.feeds_into_id}<span class="mt-1 block text-xs text-error">{fields.feeds_into_id}</span>{/if}
		</div>
		{#if lockStructure}<p class="text-xs text-muted">{t('teams.f.settings_only')}</p>{/if}
		<label class="flex items-center gap-2 text-sm">
			<input type="checkbox" name="tournament_only" class="checkbox checkbox-sm" checked={team?.tournament_only ?? false} />
			{t('teams.f.tournament_only')}
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{submitLabel}</button>
		</div>
	</form>
</Modal>
