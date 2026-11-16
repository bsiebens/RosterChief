<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ShieldCheck, Usb } from '@lucide/svelte';
	import { auth, getCredential, splitErrors, webauthnMessage, webauthnSupported } from '#lib/api.ts';
	import { safeNext } from '#lib/next.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { afterSignIn } from '#lib/session.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import OtpInput from '#lib/components/OtpInput.svelte';

	let { data } = $props();

	let code = $state('');
	let busy = $state(false);
	let error = $state('');
	let formErrors = $state<string[]>([]);

	const next = $derived(safeNext(page.url.searchParams.get('next')));
	const hasCode = $derived(data.types.includes('totp') || data.types.includes('recovery_codes'));
	const hasKey = $derived(data.types.includes('webauthn') && webauthnSupported());

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		formErrors = [];
		const r = await auth('POST', '/auth/2fa/authenticate', { code: code.trim() });
		if (r.ok) await afterSignIn(next, page.data.mode);
		else if (r.status === 401 && !r.flows?.some((f) => f.id === 'mfa_authenticate')) await goto('/login', { refreshAll: true });
		else {
			const e = splitErrors(r);
			error = e.fields.code ?? '';
			formErrors = e.form;
			code = '';
		}
		busy = false;
	}

	async function useKey() {
		busy = true;
		formErrors = [];
		try {
			const options = await auth('GET', '/auth/webauthn/authenticate');
			const credential = options.data?.request_options && (await getCredential(options.data.request_options));
			if (!credential) return;
			const r = await auth('POST', '/auth/webauthn/authenticate', { credential });
			if (r.ok) await afterSignIn(next, page.data.mode);
			else formErrors = splitErrors(r).form;
		} catch (e) {
			formErrors = [webauthnMessage(e)];
		} finally {
			busy = false;
		}
	}

	async function cancel() {
		await auth('DELETE', '/auth/session');
		await goto('/login', { refreshAll: true });
	}
</script>

<AuthCard title={t('auth.mfa.title')} lead={t('auth.mfa.lead')}>
	{#each formErrors as message (message)}<Alert kind="error">{message}</Alert>{/each}

	{#if hasCode}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<OtpInput bind:value={code} {error} autofocus />
			<button class="btn btn-primary w-full" type="submit" disabled={busy || code.trim().length < 6}>
				<ShieldCheck size={16} aria-hidden="true" /> {t('auth.mfa.verify')}
			</button>
		</form>
	{/if}

	{#if hasKey}
		{#if hasCode}
			<div class="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
				<span class="h-px flex-1 bg-line"></span><span>{t('auth.or')}</span><span class="h-px flex-1 bg-line"></span>
			</div>
		{/if}
		<button class="btn btn-outline w-full" type="button" disabled={busy} onclick={useKey}>
			<Usb size={16} aria-hidden="true" /> {t('auth.mfa.use_key')}
		</button>
	{/if}

	{#if !hasCode && !hasKey}
		<Alert kind="warning">{t('auth.mfa.none')}</Alert>
	{/if}

	{#snippet footer()}<button class="underline hover:no-underline" onclick={cancel}>{t('auth.mfa.cancel')}</button>{/snippet}
</AuthCard>
