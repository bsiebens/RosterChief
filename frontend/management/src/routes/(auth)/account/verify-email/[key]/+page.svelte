<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { auth } from '#lib/api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import Alert from '#lib/components/Alert.svelte';

	let { data } = $props();
	let busy = $state(false);
	let failed = $state(false);

	async function confirm() {
		busy = true;
		const r = await auth('POST', '/auth/email/verify', { key: page.params.key });
		// 200 = verified and signed in, 401 = verified, sign-in still pending, 400 = bad key.
		if (r.ok || r.status === 401) await goto('/login', { refreshAll: true });
		else failed = true;
		busy = false;
	}
</script>

<AuthCard title={t('auth.verify.title')}>
	{#if !data.valid || failed}
		<Alert kind="error" title={t('auth.link_expired')}>{t('auth.verify.expired_body')}</Alert>
	{:else}
		<p class="text-sm">{t('auth.verify.body_before')} <strong class="font-mono text-[0.85em]">{data.email}</strong> {t('auth.verify.body_after')}</p>
		<button class="btn btn-primary w-full" disabled={busy} onclick={confirm}>{t('auth.verify.confirm')}</button>
	{/if}

	{#snippet footer()}<a href="/login">{t('auth.back_to_login')}</a>{/snippet}
</AuthCard>
