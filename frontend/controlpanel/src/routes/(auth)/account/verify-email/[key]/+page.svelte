<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { auth } from '#lib/api.ts';
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

<AuthCard title="Confirm email">
	{#if !data.valid || failed}
		<Alert kind="error" title="Link expired">This confirmation link is invalid or has already been used.</Alert>
	{:else}
		<p class="text-sm">Confirm that <strong class="font-mono">{data.email}</strong> is your email address.</p>
		<button class="btn btn-primary w-full" disabled={busy} onclick={confirm}>Confirm email</button>
	{/if}

	{#snippet footer()}<a href="/login">Back to sign in</a>{/snippet}
</AuthCard>
