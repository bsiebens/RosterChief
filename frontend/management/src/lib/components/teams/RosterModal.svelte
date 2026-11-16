<script lang="ts">
	import { enhance } from '$app/forms';
	import { UserPlus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Position, RosterEntry } from '#lib/teams/server.ts';

	/** Add (entry = null) or edit one player on the roster. Number clashes (422 on jersey_number) show under the number field. */
	let {
		open = $bindable(false),
		entry = null,
		positions,
		exclude,
		act,
		seasonId = '',
		form
	}: { open: boolean; entry?: RosterEntry | null; positions: Position[]; exclude: string[]; act: (action: string) => string; seasonId?: string; form: any } = $props();

	const name = $derived(entry ? 'editPlayer' : 'addPlayer');
	let busy = $state(false);
	let memberId = $state('');
	const mine = $derived(form?.teamAction === name);
	const fields = $derived<Record<string, string>>(mine ? (form?.teamFields ?? {}) : {});
	const formLevel = $derived(mine && form?.teamError && !Object.keys(fields).length ? (form.teamError as string) : '');
	const title = $derived(entry ? t('teams.player.edit_named', { name: `${entry.first_name} ${entry.last_name}`.trim() }) : t('teams.player.add'));
</script>

<Modal bind:open {title}>
	<form
		method="POST"
		action={act(name)}
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				await update({ reset: result.type === 'success' });
				busy = false;
				if (result.type === 'success') {
					memberId = '';
					open = false;
				}
			};
		}}
	>
		<input type="hidden" name="season_id" value={seasonId} />
		{#if formLevel}<Alert kind="error">{formLevel}</Alert>{/if}
		{#if entry}
			<input type="hidden" name="membership_id" value={entry.id} />
		{:else}
			<EntityPicker kind="member" name="member_id" label={t('teams.person')} {exclude} bind:value={memberId} error={fields.member_id} />
		{/if}
		<div class="grid grid-cols-[2fr_1fr] gap-3">
			<div class="form-control w-full">
				<label class="label" for="{name}-pos"><span class="label-text">{t('teams.f.position')}</span></label>
				<select id="{name}-pos" name="position_id" class="select select-bordered w-full">
					<option value="">{t('teams.f.position_none')}</option>
					{#each positions as p (p.id)}<option value={p.id} selected={p.id === entry?.position_id}>{p.name}</option>{/each}
				</select>
				{#if fields.position_id}<span class="mt-1 block text-xs text-error">{fields.position_id}</span>{/if}
			</div>
			<Field label={t('teams.f.number')} name="jersey_number" type="number" min="0" value={entry?.jersey_number === null || entry?.jersey_number === undefined ? '' : String(entry.jersey_number)} error={fields.jersey_number} />
		</div>
		<div class="flex flex-wrap gap-5 text-sm">
			<label class="flex items-center gap-2"><input type="checkbox" name="is_captain" class="checkbox checkbox-sm" checked={entry?.is_captain ?? false} /> {t('teams.captain')}</label>
			<label class="flex items-center gap-2"><input type="checkbox" name="is_alternate_captain" class="checkbox checkbox-sm" checked={entry?.is_alternate_captain ?? false} /> {t('teams.alt_captain')}</label>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy || (!entry && !memberId)}>
				{#if !entry}<UserPlus size={16} />{/if}
				{entry ? t('common.save') : t('teams.player.add_submit')}
			</button>
		</div>
	</form>
</Modal>
