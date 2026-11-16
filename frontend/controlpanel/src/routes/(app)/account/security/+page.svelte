<script lang="ts">
	import { invalidate } from '$app/navigation';
	import PasskeysCard from '#lib/components/PasskeysCard.svelte';
	import RecoveryCard from '#lib/components/RecoveryCard.svelte';
	import TotpCard from '#lib/components/TotpCard.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import type { Authenticator } from '#lib/types.ts';

	let { data } = $props();

	const totp = $derived(data.authenticators.find((a): a is Extract<Authenticator, { type: 'totp' }> => a.type === 'totp'));
	const recovery = $derived(data.authenticators.find((a): a is Extract<Authenticator, { type: 'recovery_codes' }> => a.type === 'recovery_codes'));
	const keys = $derived(data.authenticators.filter((a): a is Extract<Authenticator, { type: 'webauthn' }> => a.type === 'webauthn'));
	const hasFactor = $derived(!!totp || keys.length > 0);

	let showCodes = $state(0);
	const refresh = () => invalidate('app:authenticators');
</script>

<svelte:head><title>Two-factor authentication · RosterChief</title></svelte:head>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Two-factor authentication</h1>
	<p class="mt-1.5 text-sm text-muted">Protect your platform account with a second step at sign-in.</p>
</div>

{#if data.required && !hasFactor}
	<Alert kind="warning" title="Two-factor authentication is required">Set up an authenticator app or a security key to continue to the control panel.</Alert>
{:else if hasFactor}
	<Alert kind="success">Two-factor authentication is on for your account.</Alert>
{/if}

<div class="grid gap-4 lg:grid-cols-3 [&>.card]:flex [&>.card]:flex-col [&_.card-body]:flex-1 [&_.card-actions]:mt-auto!">
	{#if data.types.includes('totp')}
		<TotpCard authenticator={totp} onchange={refresh} onnewcodes={() => showCodes++} />
	{/if}
	{#if data.types.includes('webauthn')}
		<PasskeysCard {keys} onchange={refresh} onnewcodes={() => showCodes++} />
	{/if}
	{#if data.types.includes('recovery_codes')}
		<RecoveryCard authenticator={recovery} canGenerate={hasFactor} onchange={refresh} openRequest={showCodes} />
	{/if}
</div>

{#if hasFactor && data.required}
	<div><a class="btn btn-primary" href="/">Continue to the control panel</a></div>
{/if}
