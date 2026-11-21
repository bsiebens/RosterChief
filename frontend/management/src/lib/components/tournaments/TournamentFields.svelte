
<script lang="ts">
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { STATUSES } from '#lib/tournaments.svelte.ts';
	import type { TournamentForm } from '#lib/tournaments-form.ts';

	let { v = $bindable(), errors = {}, teams = [] }: { v: TournamentForm; errors?: Record<string, string>; teams?: { id: string; name: string }[] } = $props();
	const id = $props.id();
</script>

<div class="grid gap-3">
	<Field label={t('tn.f.name')} bind:value={v.name} error={errors.name} required maxlength={200} />
	<div class="grid gap-3 sm:grid-cols-2">
		<Field label={t('tn.f.start_date')} type="date" bind:value={v.start_date} error={errors.start_date} required />
		<Field label={t('tn.f.end_date')} type="date" bind:value={v.end_date} error={errors.end_date} required />
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="form-control w-full">
			<label class="label" for="{id}-status"><span class="label-text">{t('tn.f.status')}</span></label>
			<select id="{id}-status" class="select select-bordered w-full" bind:value={v.status}>
				{#each STATUSES as s (s)}<option value={s}>{t(`tn.status.${s}`)}</option>{/each}
			</select>
			{#if errors.status}<span class="mt-1 block text-xs text-error">{errors.status}</span>{/if}
		</div>
		<div class="form-control w-full">
			<label class="label" for="{id}-home"><span class="label-text">{t('tn.f.home_team')}</span></label>
			<select id="{id}-home" class="select select-bordered w-full" bind:value={v.home_team_id}>
				<option value="">{t('tn.f.home_team_none')}</option>
				{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
			</select>
			{#if errors.home_team_id}<span class="mt-1 block text-xs text-error">{errors.home_team_id}</span>{/if}
		</div>
	</div>
	<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3">
		<input type="checkbox" class="checkbox" bind:checked={v.registration_open} />
		<span class="text-sm font-semibold text-ink">{t('tn.f.registration_open')}</span>
	</label>
	<div class="grid gap-3 sm:grid-cols-3">
		<Field label={t('tn.f.max_teams')} type="number" min="1" bind:value={v.max_teams} error={errors.max_teams} />
		<Field label={t('tn.f.min_age')} type="number" min="0" bind:value={v.min_age} error={errors.min_age} />
		<Field label={t('tn.f.max_age')} type="number" min="0" bind:value={v.max_age} error={errors.max_age} />
	</div>
	<Field label={t('tn.f.contact_email')} type="email" bind:value={v.contact_email} error={errors.contact_email} />
	<div class="form-control w-full">
		<label class="label" for="{id}-desc"><span class="label-text">{t('tn.f.description')}</span></label>
		<textarea id="{id}-desc" class="textarea textarea-bordered min-h-28 w-full" bind:value={v.description}></textarea>
		<span class="label-text-alt mt-1 block {errors.description ? 'text-error' : ''}">{errors.description || t('tn.f.description_help')}</span>
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		<Field label={t('tn.f.entry_fee')} inputmode="decimal" bind:value={v.entry_fee_amount} error={errors.entry_fee_amount} />
		<Field label={t('tn.f.deposit')} inputmode="decimal" bind:value={v.deposit_amount} error={errors.deposit_amount} />
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="form-control w-full">
			<label class="label" for="{id}-lunch"><span class="label-text">{t('tn.f.lunch_mode')}</span></label>
			<select id="{id}-lunch" class="select select-bordered w-full" bind:value={v.lunch_mode}>
				{#each ['none', 'optional', 'mandatory'] as m (m)}<option value={m}>{t(`tn.lunch.${m}`)}</option>{/each}
			</select>
			{#if errors.lunch_mode}<span class="mt-1 block text-xs text-error">{errors.lunch_mode}</span>{/if}
		</div>
		<Field label={t('tn.f.lunch_price')} inputmode="decimal" bind:value={v.lunch_price_per_day} error={errors.lunch_price_per_day} disabled={v.lunch_mode === 'none'} />
	</div>
	{#if v.lunch_mode !== 'none'}
		<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox" bind:checked={v.lunch_billed_with_fee} />
			<span class="text-sm text-ink">{t('tn.f.lunch_with_fee')}</span>
		</label>
	{/if}
	<Field label={t('tn.f.match_duration')} type="number" min="1" bind:value={v.default_match_duration_minutes} error={errors.default_match_duration_minutes} help={t('tn.f.match_duration_help')} />
</div>
