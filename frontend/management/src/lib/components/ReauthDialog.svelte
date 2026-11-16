<script lang="ts">
	import { onMount } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { auth, onReauthRequired, splitErrors } from '#lib/api.ts';
	import Modal from './Modal.svelte';
	import Alert from './Alert.svelte';
	import Field from './Field.svelte';
	import OtpInput from './OtpInput.svelte';

	let open = $state(false);
	let pending: ((ok: boolean) => void) | null = null;
	let mfaTypes = $state<string[]>([]);
	let useCode = $state(false);
	let password = $state('');
	let code = $state('');
	let error = $state('');
	let busy = $state(false);

	onMount(() => {
		onReauthRequired((r) => {
			pending = r.resolve;
			mfaTypes = r.mfaTypes;
			useCode = false;
			password = code = error = '';
			open = true;
		});
		return () => onReauthRequired(null);
	});

	function settle(ok: boolean) {
		const p = pending;
		pending = null;
		open = false;
		p?.(ok);
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		const r = useCode ? await auth('POST', '/auth/2fa/reauthenticate', { code: code.trim() }) : await auth('POST', '/auth/reauthenticate', { password });
		if (r.ok) settle(true);
		else {
			const e = splitErrors(r);
			error = e.fields.password ?? e.fields.code ?? e.form[0] ?? t('account.reauth.failed');
			password = code = '';
		}
		busy = false;
	}
</script>

<Modal bind:open title={t('account.reauth.title')} onclose={() => pending && settle(false)}>
	<form class="flex flex-col gap-4" onsubmit={submit}>
		<p class="text-sm text-muted">{t('account.reauth.sensitive')} {useCode ? t('account.reauth.enter_code') : t('account.reauth.enter_password')}</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if useCode}
			<OtpInput bind:value={code} autofocus />
		{:else}
			<Field label={t('auth.password')} type="password" autocomplete="current-password" required bind:value={password} />
		{/if}
		{#if mfaTypes.includes('totp') || mfaTypes.includes('recovery_codes')}
			<button type="button" class="btn-link self-start text-sm" onclick={() => ((useCode = !useCode), (error = ''))}>
				{useCode ? t('account.reauth.use_password') : t('account.reauth.use_code')}
			</button>
		{/if}
		<div class="modal-action mt-2">
			<button type="button" class="btn btn-outline" onclick={() => settle(false)}>{t('common.cancel')}</button>
			<button type="submit" class="btn btn-primary" disabled={busy}>{t('account.reauth.continue')}</button>
		</div>
	</form>
</Modal>
