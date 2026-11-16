<script lang="ts">
	import { invalidate } from '$app/navigation';
	import PasskeysCard from '#lib/components/PasskeysCard.svelte';
	import RecoveryCard from '#lib/components/RecoveryCard.svelte';
	import TotpCard from '#lib/components/TotpCard.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import type { Authenticator } from '#lib/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();

	const totp = $derived(data.authenticators.find((a): a is Extract<Authenticator, { type: 'totp' }> => a.type === 'totp'));
	const recovery = $derived(data.authenticators.find((a): a is Extract<Authenticator, { type: 'recovery_codes' }> => a.type === 'recovery_codes'));
	const keys = $derived(data.authenticators.filter((a): a is Extract<Authenticator, { type: 'webauthn' }> => a.type === 'webauthn'));
	const hasFactor = $derived(!!totp || keys.length > 0);

	let showCodes = $state(0);
	const refresh = () => invalidate('app:authenticators');

	usePageHeader(() => ({ title: t('account.security.title') }));
</script>

<svelte:head><title>{pageTitle(t('account.security.title'))}</title></svelte:head>

<p class="text-sm text-muted">{t('account.security.lead')}</p>

{#if data.required && !hasFactor}
	<Alert kind="warning" title={t('account.security.required_title')}>{t('account.security.required_body')}</Alert>
{:else if hasFactor}
	<Alert kind="success">{t('account.security.on')}</Alert>
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
	<div><a class="btn btn-primary" href="/">{t('account.security.continue')}</a></div>
{/if}
