<script lang="ts">
	import { enhance } from '$app/forms';
	import { UserPlus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * "Add parent" / "Add child": pick an existing person and add them to a family with a fixed role.
	 * Posts `member_id`, `role` (and `family_id` when given) to the page action `action` (e.g. `?/addMember`).
	 */
	let {
		open = $bindable(false),
		role,
		action,
		familyId,
		exclude = [],
		error,
		fields = {}
	}: { open: boolean; role: 'parent' | 'child'; action: string; familyId?: string; exclude?: string[]; error?: string; fields?: Record<string, string> } = $props();
	let mode = $state<'existing' | 'new'>('existing');

	let memberId = $state('');
	let busy = $state(false);
</script>

<Modal bind:open title={t(role === 'parent' ? 'families.add_parent' : 'families.add_child')}>
	<form
		method="POST"
		{action}
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				await update();
				busy = false;
				if (result.type === 'success') {
					memberId = '';
					open = false;
				}
			};
		}}
	>
		<div class="flex w-fit items-center gap-1 rounded-full bg-primary p-1" role="group" aria-label={t(role === 'parent' ? 'families.add_parent' : 'families.add_child')}>
			{#each [['existing', t('families.person_existing')], ['new', t('families.person_new')]] as [key, label] (key)}
				<button
					type="button"
					aria-pressed={mode === key}
					onclick={() => (mode = key as 'existing' | 'new')}
					class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {mode === key ? 'bg-white text-ink' : 'text-primary-content hover:opacity-80'}">{label}</button>
			{/each}
		</div>
		{#if error && !Object.keys(fields).length}<Alert kind="error">{error}</Alert>{/if}
		<input type="hidden" name="role" value={role} />
		<input type="hidden" name="mode" value={mode} />
		{#if familyId}<input type="hidden" name="family_id" value={familyId} />{/if}
		{#if mode === 'existing'}
			<EntityPicker kind="member" name="member_id" label={t('families.person')} {exclude} bind:value={memberId} />
		{:else}
			<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
				<Field label={t('members.form.first_name')} name="first_name" required error={fields.first_name} autocomplete="off" />
				<Field label={t('members.form.last_name')} name="last_name" required error={fields.last_name} autocomplete="off" />
				<Field label={t('members.form.email')} name="email" type="email" error={fields.email} help={t('families.person_new_help')} autocomplete="off" />
				<Field label={t('members.form.dob')} name="date_of_birth" type="date" error={fields.date_of_birth} />
			</div>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy || (mode === 'existing' && !memberId)}><UserPlus size={16} /> {t('families.add_person_submit')}</button>
		</div>
	</form>
</Modal>
