<script lang="ts">
	import { platform } from '#lib/api.ts';
	import { fieldError, type UserDetail } from '#lib/users.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	export type Action = 'password' | 'mfa' | 'block' | 'unblock';

	let { user, action = $bindable(null), ondone }: { user: UserDetail; action: Action | null; ondone: (message: string) => void } = $props();

	const acc = $derived(user.account);
	const staff = $derived(!!acc.platform_level);

	let open = $state(false);
	let reason = $state('');
	let notify = $state(true);
	let invalidate = $state(true);
	let disableCurrent = $state(false);
	let error = $state('');
	let reasonError = $state('');
	let busy = $state(false);
	let key = crypto.randomUUID();

	// Reset the form each time a dialog opens.
	$effect(() => {
		open = action !== null;
		if (action !== null) {
			reason = '';
			notify = true;
			invalidate = true;
			disableCurrent = false;
			error = reasonError = '';
			key = crypto.randomUUID();
		}
	});

	const close = () => (action = null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!action || busy) return;
		const text = reason.trim();
		const required = action === 'mfa' || action === 'block';
		if (required && !text) return (reasonError = 'Give a reason.');
		const path = { password: 'reset-password', mfa: 'reset-2fa', block: 'block', unblock: 'unblock' }[action];
		const body =
			action === 'mfa'
				? { reason: text, notify }
				: action === 'password'
					? { ...(text ? { reason: text } : {}), invalidate_sessions: invalidate, disable_current_password: disableCurrent }
					: { ...(text || required ? { reason: text } : {}) };
		busy = true;
		error = reasonError = '';
		const r = await platform<{ note?: string; sent_to?: string; notified?: boolean }>('POST', `/users/${acc.id}/${path}`, body, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (!r.ok) {
			if (r.status !== 0) key = crypto.randomUUID();
			reasonError = fieldError(r.fields, 'reason') ?? '';
			error = reasonError ? '' : (r.message ?? 'That did not work.');
			return;
		}
		const note = r.data?.note ?? '';
		const lead = { password: `Password reset email sent to ${r.data?.sent_to ?? acc.email}.`, mfa: 'Two-factor authentication was reset.', block: 'Account blocked.', unblock: 'Account unblocked.' }[action];
		action = null;
		ondone(`${lead} ${note}`.trim());
	}

	const TITLE = { password: 'Send password reset email', mfa: 'Reset two-factor', block: 'Block account', unblock: 'Unblock account' };
</script>

<Modal bind:open title={action ? TITLE[action] : ''} onclose={close}>
	{#if action}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<p class="text-sm text-muted">Account: <strong class="text-ink">{acc.email}</strong></p>

			{#if action === 'password'}
				<p class="text-sm">We send the standard password reset email to {acc.email}. You never see or set a password.</p>
				<label class="flex items-start gap-3 text-sm">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={invalidate} />
					<span><strong class="text-ink">Sign them out everywhere</strong><br /><span class="text-muted">All their active sessions end now.</span></span>
				</label>
				<label class="flex items-start gap-3 text-sm">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={disableCurrent} />
					<span><strong class="text-ink">Stop their current password working immediately</strong><br /><span class="text-muted">The link in the email is then their only way in.</span></span>
				</label>
				<Alert kind="info">
					{invalidate ? 'They are signed out everywhere. ' : 'Their sessions stay active. '}{disableCurrent ? 'Their current password stops working until they use the email link.' : 'Their current password keeps working until they choose a new one.'}
				</Alert>
			{:else if action === 'mfa'}
				<Alert kind="error" title="Removes all methods">
					This removes every two-factor method: authenticator app, security keys and passkeys, and recovery codes. Their password and sessions are not touched.
					{#if staff}As platform {acc.platform_level}, they must set up two-factor again before they can use the control panel.{/if}
				</Alert>
				<label class="flex items-start gap-3 text-sm">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={notify} />
					<span><strong class="text-ink">Email the user about this</strong><br /><span class="text-muted">A notice without links.</span></span>
				</label>
			{:else if action === 'block'}
				<p class="text-sm">The user is signed out everywhere immediately and cannot sign in until you unblock the account. Their data and club memberships are kept.</p>
			{:else}
				<p class="text-sm">The user can sign in again with their current credentials.</p>
			{/if}

			<div class="form-control w-full">
				<label class="label" for="user-action-reason"><span class="label-text">Reason{action === 'mfa' || action === 'block' ? '' : ' (optional)'}</span></label>
				<textarea id="user-action-reason" class="textarea textarea-bordered w-full {reasonError ? 'textarea-error' : ''}" rows="2" maxlength="300" bind:value={reason} aria-invalid={reasonError ? 'true' : undefined}></textarea>
				{#if reasonError}<span class="mt-1 block text-xs text-club-dark">{reasonError}</span>{:else}<span class="label-text-alt mt-1 block">Recorded in the audit log. {reason.length}/300</span>{/if}
			</div>

			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="modal-action mt-0">
				<button type="button" class="btn btn-outline" onclick={close}>Cancel</button>
				{#if action === 'password'}
					<button type="submit" class="btn btn-primary" disabled={busy}>Send reset email</button>
				{:else if action === 'unblock'}
					<button type="submit" class="btn btn-success" disabled={busy}>Unblock account</button>
				{:else}
					<button type="submit" class="btn btn-error" disabled={busy}>{action === 'mfa' ? 'Reset two-factor' : 'Block account'}</button>
				{/if}
			</div>
		</form>
	{/if}
</Modal>
