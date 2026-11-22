
<script lang="ts">
	import DaysEditor from '#lib/components/tournaments/DaysEditor.svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { Hourglass, Timer } from '@lucide/svelte';
	import { STATUSES } from '#lib/tournaments.svelte.ts';
	import type { TournamentForm } from '#lib/tournaments-form.ts';

	let { v = $bindable(), errors = {}, teams = [], part }: { v: TournamentForm; errors?: Record<string, string>; teams?: { id: string; name: string }[]; part: 'basics' | 'days' | 'entries' | 'fees' } = $props();
	const id = $props.id();
</script>

<div class="grid gap-3">
{#if part === 'basics'}
	<Field label={t('tn.f.name')} bind:value={v.name} error={errors.name} required maxlength={200} />
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="form-control w-full">
			<label class="label" for="{id}-home"><span class="label-text">{t('tn.f.home_team')}</span></label>
			<select id="{id}-home" class="select select-bordered w-full" bind:value={v.home_team_id}>
				<option value="">{t('tn.f.home_team_none')}</option>
				{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
			</select>
			{#if errors.home_team_id}<span class="mt-1 block text-xs text-error">{errors.home_team_id}</span>{/if}
		</div>
	</div>
	<Field label={t('tn.f.contact_email')} type="email" bind:value={v.contact_email} error={errors.contact_email} />
	<div class="form-control w-full">
		<label class="label" for="{id}-desc"><span class="label-text">{t('tn.f.description')}</span></label>
		<textarea id="{id}-desc" class="textarea textarea-bordered min-h-28 w-full" bind:value={v.description}></textarea>
		<span class="label-text-alt mt-1 block {errors.description ? 'text-error' : ''}">{errors.description || t('tn.f.description_help')}</span>
	</div>
{:else if part === 'days'}
	<DaysEditor bind:days={v.days} error={errors.days} />
{:else if part === 'entries'}
	<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3">
		<input type="checkbox" class="checkbox" bind:checked={v.registration_open} />
		<span class="text-sm font-semibold text-ink">{t('tn.f.registration_open')}</span>
	</label>
	<div class="grid gap-3 sm:grid-cols-3">
		<Field label={t('tn.f.max_teams')} type="number" min="1" bind:value={v.max_teams} error={errors.max_teams} />
		<Field label={t('tn.f.earliest_birth_year')} type="number" min="1900" placeholder="2012" bind:value={v.earliest_birth_year} error={errors.earliest_birth_year} />
		<Field label={t('tn.f.latest_birth_year')} type="number" min="1900" placeholder="2016" bind:value={v.latest_birth_year} error={errors.latest_birth_year} />
	</div>
	<div class="grid gap-3 sm:grid-cols-2">
		<Field label={t('tn.f.match_duration')} type="number" min="1" bind:value={v.default_match_duration_minutes} error={errors.default_match_duration_minutes} help={t('tn.f.match_duration_help')} />
		<Field label={t('tn.f.gap')} type="number" min="0" bind:value={v.default_gap_minutes} error={errors.default_gap_minutes} help={t('tn.f.gap_help')} />
	</div>
	<fieldset class="grid gap-2">
		<legend class="label-text text-sm font-medium">{t('tn.f.clock')}</legend>
		<div class="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label={t('tn.f.clock')}>
			{#each [{ key: 'up', icon: Timer, from: '0:00', to: '20:00' }, { key: 'down', icon: Hourglass, from: '20:00', to: '0:00' }] as opt (opt.key)}
				{@const Icon = opt.icon}
				<button type="button" role="radio" aria-checked={v.clock_direction === opt.key} onclick={() => (v.clock_direction = opt.key as 'up' | 'down')} class="flex cursor-pointer items-start gap-3 rounded-xl border-2 p-4 text-left transition-colors {v.clock_direction === opt.key ? 'border-club bg-club/10' : 'border-line bg-white hover:border-ink/40'}">
					<span class="flex size-11 shrink-0 items-center justify-center rounded-lg {v.clock_direction === opt.key ? 'bg-club text-club-content' : 'bg-subhead text-muted'}"><Icon size={22} aria-hidden="true" /></span>
					<span class="grid min-w-0 gap-1">
						<span class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{t(`tn.f.clock_${opt.key}`)}</span>
						<span class="font-mono text-xs font-semibold text-ink/70">{opt.from} → {opt.to}</span>
						<span class="text-xs text-muted">{t(`tn.f.clock_${opt.key}_help`)}</span>
					</span>
				</button>
			{/each}
		</div>
		{#if errors.clock_direction}<span class="text-xs text-error">{errors.clock_direction}</span>{/if}
	</fieldset>
{:else}
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
{/if}
</div>
