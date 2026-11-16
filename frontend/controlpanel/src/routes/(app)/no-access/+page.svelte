<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';

	async function signOut() {
		await auth('DELETE', '/auth/session');
		await goto('/login', { refreshAll: true });
	}
</script>

<svelte:head><title>No access · RosterChief</title></svelte:head>

<div class="max-w-xl">
	<Alert kind="warning" title="No platform access">This account is not platform staff. Ask a platform admin to grant you access, then sign in again.</Alert>
	<button class="btn btn-outline mt-4" onclick={signOut}>Sign out</button>
</div>
