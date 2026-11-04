<script lang="ts">
	import { enhance } from '$app/forms';
	import { KeyRound } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Confirm granting a login. Without an `email` on the member it asks for one; the server saves it on the member and
	 * then grants the login. `memberId` is only needed where the page action serves several members (the family page).
	 */
	let {
		open = $bindable(false),
		email = '',
		memberId,
		name = '',
		action = '?/grantLogin',
		error,
		fields = {}
	}: { open: boolean; email?: string; memberId?: string; name?: string; action?: string; error?: string; fields?: Record<string, string> } = $props();
	let busy = $state(false);
</script>

<Modal bind:open title={t('members.detail.grant.title')}>
	<form
		method="POST"
		{action}
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
		{#if error && !fields.email}<Alert kind="error" title={t('members.detail.grant.failed')}>{error}</Alert>{/if}
		{#if memberId}<input type="hidden" name="member_id" value={memberId} />{/if}
		{#if email}
			<p class="text-sm">{t('members.detail.grant.blurb', { email })}</p>
		{:else}
			<p class="text-sm">{t('members.detail.grant.needs_email', { name })}</p>
			<Field label={t('members.form.email')} name="email" type="email" required error={fields.email} autocomplete="off" />
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><KeyRound size={16} /> {t('members.detail.grant.submit')}</button>
		</div>
	</form>
</Modal>
