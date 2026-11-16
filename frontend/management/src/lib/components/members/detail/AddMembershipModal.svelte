<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { statusLabel } from '#lib/members/detail-helpers.ts';
	import type { Season } from '#lib/members/detail-types.ts';

	let { open = $bindable(false), season, error, fields = {} }: { open: boolean; season: Season; error?: string; fields?: Record<string, string> } = $props();
	let busy = $state(false);
</script>

<Modal bind:open title={t('members.detail.add_membership.title', { season: season.name })}>
	<form
		method="POST"
		action="?/addMembership"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				await update();
				busy = false;
				if (result.type === 'success') open = false;
			};
		}}
	>
		{#if error && !Object.keys(fields).length}<Alert kind="error" title={t('members.detail.add_membership.failed')}>{error}</Alert>{/if}
		<input type="hidden" name="season_id" value={season.id} />
		<div class="form-control w-full">
			<label class="label" for="am-status"><span class="label-text">{t('members.detail.status')}</span></label>
			<select id="am-status" name="status" class="select select-bordered w-full">
				{#each ['pending', 'lapsed', 'cancelled'] as s (s)}<option value={s}>{statusLabel(s)}</option>{/each}
			</select>
			{#if fields.status}<span class="mt-1 block text-xs text-error">{fields.status}</span>{/if}
		</div>
		<Field label={t('members.detail.add_membership.fee_amount')} name="fee_amount" type="number" min="0" step="0.01" value="0" error={fields.fee_amount} />
		<Field label={t('members.detail.add_membership.deadline')} name="early_payment_deadline" type="date" error={fields.early_payment_deadline} />
		<Field label={t('members.detail.add_membership.discount')} name="early_payment_discount" type="number" min="0" step="0.01" value="0" error={fields.early_payment_discount} />
		<Field label={t('members.detail.add_membership.signed_up_at')} name="signed_up_at" type="date" help={t('members.detail.add_membership.signed_up_help')} error={fields.signed_up_at} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Plus size={16} /> {t('members.detail.add_membership.submit')}</button>
		</div>
	</form>
</Modal>
