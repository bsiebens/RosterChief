<script lang="ts">
	import { invalidate } from '$app/navigation';
	import Alert from '#lib/components/Alert.svelte';
	import ProviderConfig from '#lib/components/push/ProviderConfig.svelte';
	import SettingsForm from '#lib/components/push/SettingsForm.svelte';

	let { data } = $props();

	const c = $derived(data.config);
	const refresh = () => invalidate('app:push');
</script>

<svelte:head><title>Push configuration · RosterChief</title></svelte:head>

{#if data.error || !c}
	<Alert kind="error">{data.error || 'The configuration could not be loaded.'}</Alert>
{:else}
	<Alert kind="info" title="Credentials live in the environment">
		API keys and certificates are set as environment variables on the server and never shown here. This page shows whether they are present and lets you check that they work. Validating never sends a push.
	</Alert>
	<div class="grid gap-4 md:grid-cols-3">
		{#each c.providers as p (p.provider)}<ProviderConfig provider={p} onToggled={refresh} />{/each}
	</div>
	<Alert kind="info" title="Sandbox default">
		The sandbox is {c.sandbox_default ? 'on' : 'off'} by default when the server starts (PUSH_SANDBOX). Switching it on or off at runtime is done on the <a class="link font-semibold" href="/push/sandbox">Sandbox tab</a>.
	</Alert>
	<SettingsForm settings={data.config!.settings} onsaved={refresh} />
{/if}
