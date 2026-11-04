<script lang="ts">
	import { enhance } from '$app/forms';
	import { Pencil, Plus, Trash2 } from '@lucide/svelte';
	import { kindIcon } from '#lib/officiating-icons.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import type { EligibilityProfile, NamedRef } from '#lib/members/eligibility.ts';

	/** Referee or official eligibility of a member: level, validity and the teams it qualifies for. Everyone reads it; only members.manage_members edits. Feature-gated by the page. */
	let {
		kind,
		profile,
		canEdit = false,
		levels,
		teams,
		form
	}: { kind: 'referee' | 'official'; profile: EligibilityProfile | null; levels: NamedRef[]; teams: NamedRef[]; canEdit?: boolean; form?: any } = $props();

	const KindIcon = $derived(kindIcon[kind]);
	let open = $state(false);
	let busy = $state(false);
	const mine = $derived(form?.eligibilityKind === kind);
	const error = $derived(mine ? (form?.eligibilityError as string | undefined) : undefined);
	const fields = $derived<Record<string, string>>(mine ? (form?.eligibilityFields ?? {}) : {});
	const excluded = $derived(new Set((profile?.excluded_teams ?? []).map((x) => x.id)));
	const run = () => {
		busy = true;
		return async ({ result, update }: { result: { type: string }; update: () => Promise<void> }) => {
			await update();
			busy = false;
			if (result.type === 'success') open = false;
		};
	};
</script>

<div class="card card-body">
	<div class="flex items-center justify-between gap-2">
		<h2 class="card-title"><KindIcon size={18} /> {t(`eligibility.${kind}.title`)}</h2>
		{#if !canEdit}
			<!-- read-only: members.manage_members is needed to change eligibility -->
		{:else if profile}
			<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (open = true)}><Pencil size={16} /> {t('common.edit')}</button>
		{:else}
			<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (open = true)}><Plus size={16} /> {t('eligibility.setup')}</button>
		{/if}
	</div>

	{#if profile}
		<dl>
			<div class="flex items-center justify-between gap-4 py-2">
				<dt class="text-sm text-muted">{t('eligibility.level')}</dt>
				<dd class="font-semibold text-ink">{profile.level?.name ?? '—'}</dd>
			</div>
			<div class="flex items-center justify-between gap-4 py-2">
				<dt class="text-sm text-muted">{t('eligibility.valid_until')}</dt>
				<dd class="flex items-center gap-2">
					<span class="font-mono text-xs font-semibold text-ink">{profile.valid_until ? formatDate(profile.valid_until) : '—'}</span>
					{#if profile.valid_until}<span class="badge badge-sm {profile.is_valid ? 'badge-success' : 'badge-error'}">{t(profile.is_valid ? 'eligibility.valid' : 'eligibility.expired')}</span>{/if}
				</dd>
			</div>
		</dl>
		<div>
			<h3 class="mb-2 font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t(`eligibility.${kind}.can`)}</h3>
			{#if profile.eligible_teams.length}
				<div class="flex flex-wrap gap-2">
					{#each profile.eligible_teams as team (team.id)}<span class="badge badge-outline rounded-sm font-display text-xs font-bold tracking-[.06em] uppercase">{team.name}</span>{/each}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('eligibility.no_teams')}</p>
			{/if}
		</div>
		{#if kind === 'referee' && profile.excluded_teams.length}
			<div>
				<h3 class="mb-2 font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('eligibility.not_allowed')}</h3>
				<div class="flex flex-wrap gap-2">
					{#each profile.excluded_teams as team (team.id)}<span class="badge badge-outline badge-error rounded-sm font-display text-xs font-bold tracking-[.06em] uppercase">{team.name}</span>{/each}
				</div>
			</div>
		{/if}
	{:else}
		<p class="text-sm text-muted">{t(`eligibility.${kind}.none`)}</p>
	{/if}
</div>

{#if canEdit}
<Modal bind:open title={t(`eligibility.${kind}.title`)}>
	<form method="POST" action="?/saveEligibility" class="grid gap-3" use:enhance={run}>
		{#if error && !Object.keys(fields).length}<Alert kind="error" title={t('eligibility.failed')}>{error}</Alert>{/if}
		<input type="hidden" name="kind" value={kind} />
		<div class="form-control w-full">
			<label class="label" for="el-level-{kind}"><span class="label-text">{t('eligibility.level')}</span></label>
			<select id="el-level-{kind}" name="level_id" class="select select-bordered w-full {fields.level_id ? 'select-error' : ''}" value={profile?.level?.id ?? ''}>
				<option value="">{t('eligibility.no_level')}</option>
				{#each levels as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
			</select>
			{#if fields.level_id}<span class="mt-1 block text-xs text-error">{fields.level_id}</span>{/if}
		</div>
		<Field label={t('eligibility.valid_until')} name="valid_until" type="date" value={profile?.valid_until ?? ''} error={fields.valid_until} />
		{#if kind === 'referee' && teams.length}
			<fieldset class="grid gap-1">
				<legend class="label"><span class="label-text">{t('eligibility.not_allowed')}</span></legend>
				<p class="mb-1 text-xs text-muted">{t('eligibility.excluded_help')}</p>
				<div class="grid max-h-44 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2">
					{#each teams as team (team.id)}
						<label class="label cursor-pointer justify-start gap-2">
							<input type="checkbox" class="checkbox checkbox-sm" name="excluded_team_ids" value={team.id} checked={excluded.has(team.id)} />
							<span class="label-text">{team.name}</span>
						</label>
					{/each}
				</div>
				{#if fields.excluded_team_ids}<span class="text-xs text-error">{fields.excluded_team_ids}</span>{/if}
			</fieldset>
		{/if}
		<div class="modal-action mt-2 flex items-center justify-end gap-2">
			{#if profile}
				<button class="btn btn-outline btn-error mr-auto gap-2" type="submit" formaction="?/removeEligibility" disabled={busy}><Trash2 size={16} /> {t('eligibility.remove')}</button>
			{/if}
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
{/if}
