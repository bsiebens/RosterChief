<script lang="ts">
	import { enhance } from '$app/forms';
	import { tick } from 'svelte';
	import { UserPlus } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Position, RosterEntry, StaffEntry } from '#lib/teams/server.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import CoachMemberPicker from '#lib/components/coach/CoachMemberPicker.svelte';

	/** One bottom sheet for the four squad edits: add or edit a player, add or edit a staff member. */
	let {
		mode,
		open,
		onclose,
		teamId,
		seasonId,
		positions,
		exclude,
		player = null,
		staff = null
	}: { mode: 'player' | 'staff'; open: boolean; onclose: () => void; teamId: string; seasonId: string; positions: Position[]; exclude: string[]; player?: RosterEntry | null; staff?: StaffEntry | null } = $props();

	const entry = $derived(mode === 'player' ? player : staff);
	const action = $derived(`${mode === 'player' ? (entry ? 'editPlayer' : 'addPlayer') : entry ? 'editStaff' : 'addStaff'}`);
	const choices = $derived(positions.filter((position) => position.staff_position === (mode === 'staff')));
	const title = $derived(entry ? (mode === 'player' ? t('teams.player.edit_named', { name: `${entry.first_name} ${entry.last_name}` }) : t('teams.staff.edit_named', { name: `${entry.first_name} ${entry.last_name}` })) : mode === 'player' ? t('teams.player.add') : t('teams.staff.add'));
	let busy = $state(false);
	let memberId = $state('');
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let numberTaken = $state(false);
	let override = $state(false);
	let formEl = $state<HTMLFormElement>();
	const addAnyway = async () => {
		override = true;
		await tick();
		formEl?.requestSubmit();
	};
	const id = $props.id();

	$effect(() => {
		if (open) {
			memberId = '';
			error = '';
			fields = {};
			numberTaken = false;
			override = false;
		}
	});
</script>

<Sheet {title} {open} {onclose}>
	<form
		method="POST"
		bind:this={formEl}
		action="?tab=teams&team={teamId}&/{action}"
		class="grid min-w-0 gap-4 px-4 pb-5"
		use:enhance={() => {
			busy = true;
			error = '';
			fields = {};
			numberTaken = false;
			return async ({ result, update }) => {
				if (result.type === 'failure') {
					fields = (result.data?.teamFields as Record<string, string>) ?? {};
					numberTaken = result.data?.teamCode === 'number_taken';
					override = false;
					error = Object.keys(fields).length ? '' : String(result.data?.teamError ?? t('common.error.generic'));
				} else if (result.type === 'success') {
					// Wait for the refreshed squad before closing, so the list never shows the old state.
					await update({ reset: false });
					onclose();
				} else await update();
				busy = false;
			};
		}}
	>
		<h2 class="font-display text-lg font-extrabold text-ink">{title}</h2>
		<input type="hidden" name="team_id" value={teamId} />
		<input type="hidden" name="season_id" value={seasonId} />
		{#if override}<input type="hidden" name="override_number" value="on" />{/if}
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if entry}
			<input type="hidden" name={mode === 'player' ? 'membership_id' : 'assignment_id'} value={entry.id} />
		{:else}
			<CoachMemberPicker name="member_id" {seasonId} {exclude} bind:value={memberId} error={fields.member_id} />
		{/if}
		<div class="grid min-w-0 gap-3 {mode === 'player' ? 'grid-cols-[minmax(0,2fr)_minmax(0,1fr)]' : ''}">
			<div class="form-control w-full">
				<label class="label" for="{id}-pos"><span class="label-text">{t('teams.f.position')}</span></label>
				<select id="{id}-pos" name="position_id" class="select select-bordered w-full" required={mode === 'staff'}>
					{#if mode === 'player'}<option value="">{t('teams.f.position_none')}</option>{:else}<option value="" disabled selected={!entry?.position_id}>{t('teams.f.position_pick')}</option>{/if}
					{#each choices as position (position.id)}<option value={position.id} selected={position.id === entry?.position_id}>{position.name}</option>{/each}
				</select>
				{#if fields.position_id}<span class="mt-1 block text-xs text-error">{fields.position_id}</span>{/if}
			</div>
			{#if mode === 'player'}
				<Field label={t('teams.f.number')} name="jersey_number" type="number" min="0" inputmode="numeric" value={player?.jersey_number == null ? '' : String(player.jersey_number)} error={numberTaken ? undefined : fields.jersey_number} />
			{/if}
		</div>
		{#if mode === 'player'}
			<div class="grid gap-1 text-sm">
				<label class="flex min-h-11 items-center gap-3"><input type="checkbox" name="is_captain" class="checkbox" checked={player?.is_captain ?? false} /> {t('teams.captain')}</label>
				<label class="flex min-h-11 items-center gap-3"><input type="checkbox" name="is_alternate_captain" class="checkbox" checked={player?.is_alternate_captain ?? false} /> {t('teams.alt_captain')}</label>
				{#if fields.is_alternate_captain}<span class="text-xs text-error">{fields.is_alternate_captain}</span>{/if}
			</div>
		{/if}
		{#if numberTaken}
			<Alert kind="warning" title={t('teams.number_taken.title')}>
				<p>{fields.jersey_number}</p>
				<p class="mt-1">{t('teams.number_taken.body')}</p>
				<button class="btn btn-warning mt-2 min-h-11" type="button" disabled={busy} onclick={addAnyway}>{entry ? t('teams.number_taken.save_anyway') : t('teams.number_taken.add_anyway')}</button>
			</Alert>
		{/if}
		<div class="grid grid-cols-2 gap-2">
			<button class="btn btn-outline min-h-11" type="button" onclick={onclose}>{t('common.cancel')}</button>
			<button class="btn btn-primary min-h-11 gap-2" type="submit" disabled={busy || (!entry && !memberId)}>
				{#if !entry}<UserPlus size={16} aria-hidden="true" />{/if}
				{entry ? t('common.save') : mode === 'player' ? t('teams.player.add_submit') : t('teams.staff.add_submit')}
			</button>
		</div>
	</form>
</Sheet>
