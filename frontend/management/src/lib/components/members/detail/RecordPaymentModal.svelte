<script lang="ts">
	import { enhance } from '$app/forms';
	import { Banknote } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let { open = $bindable(false), membershipId, remaining, error, fields = {} }: { open: boolean; membershipId: string; remaining: string; error?: string; fields?: Record<string, string> } = $props();
	let busy = $state(false);
	const METHODS = ['bank_transfer', 'cash', 'card', 'other'];
</script>

<Modal bind:open title={t('members.detail.pay.title')}>
	<form
		method="POST"
		action="?/recordPayment"
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
		{#if error && !Object.keys(fields).length}<Alert kind="error" title={t('members.detail.pay.failed')}>{error}</Alert>{/if}
		<input type="hidden" name="membership_id" value={membershipId} />
		<Field label={t('members.detail.pay.amount')} name="amount" type="number" min="0.01" step="0.01" value={remaining} required error={fields.amount} />
		<div class="form-control w-full">
			<label class="label" for="pay-method"><span class="label-text">{t('members.detail.pay.method')}</span></label>
			<select id="pay-method" name="method" class="select select-bordered w-full">
				{#each METHODS as m (m)}<option value={m}>{t(`members.detail.pay.method.${m}`)}</option>{/each}
			</select>
			{#if fields.method}<span class="mt-1 block text-xs text-error">{fields.method}</span>{/if}
		</div>
		<Field label={t('members.detail.pay.reference')} name="reference" error={fields.reference} autocomplete="off" />
		<Field label={t('members.detail.pay.paid_at')} name="paid_at" type="datetime-local" help={t('members.detail.pay.paid_at_help')} error={fields.paid_at} />
		<Field label={t('members.detail.pay.note')} name="note" error={fields.note} autocomplete="off" />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Banknote size={16} /> {t('members.detail.pay.submit')}</button>
		</div>
	</form>
</Modal>
