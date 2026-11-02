<script lang="ts">
	import { invalidate } from '$app/navigation';
	import Alert from '#lib/components/Alert.svelte';
	import Composer from '#lib/components/push/Composer.svelte';
	import SandboxEvents from '#lib/components/push/SandboxEvents.svelte';
	import SandboxMessages from '#lib/components/push/SandboxMessages.svelte';
	import SandboxMode from '#lib/components/push/SandboxMode.svelte';
	import SandboxScenario from '#lib/components/push/SandboxScenario.svelte';

	let { data } = $props();

	const o = $derived(data.overview);
	const refresh = () => invalidate('app:push');
</script>

<svelte:head><title>Push sandbox · RosterChief</title></svelte:head>

{#if data.error || !o}
	<Alert kind="error">{data.error || 'The sandbox could not be loaded.'}</Alert>
{:else}
	<SandboxMode sandbox={o.state} onchanged={refresh} />
{/if}

<div class="grid items-start gap-4 xl:grid-cols-2">
	<div class="flex min-w-0 flex-col gap-4">
		{#if o}<SandboxScenario overview={o} onchanged={refresh} />{/if}
		<SandboxMessages messages={data.messages} query={data.query} clubs={data.clubs} error={data.messagesError} />
		{#if o}<SandboxEvents events={o.events} />{/if}
	</div>
	<div class="flex min-w-0 flex-col gap-4">
		<Composer initial={data.target} sandboxOn={!!o?.state.effective} />
	</div>
</div>
