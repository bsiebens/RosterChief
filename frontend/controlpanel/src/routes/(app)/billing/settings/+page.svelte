<script lang="ts">
	import type { IssuerSettings } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import InvoicingSwitch from '#lib/components/billing/settings/InvoicingSwitch.svelte';
	import IssuerForm from '#lib/components/billing/settings/IssuerForm.svelte';
	import Readiness from '#lib/components/billing/settings/Readiness.svelte';
	import Rules from '#lib/components/billing/settings/Rules.svelte';

	let { data } = $props();

	const canEdit = $derived(data.auth.status === 'authenticated' && data.auth.me.level === 'superuser');
	let saved = $state<IssuerSettings | null>(null);
	const current = $derived(saved ?? data.settings);
</script>

<svelte:head><title>Invoicing · RosterChief</title></svelte:head>

{#if data.error || !current}
	<Alert kind="error">{data.error || 'The settings could not be loaded.'}</Alert>
{:else}
	<InvoicingSwitch enabled={current.auto_invoicing !== false} {canEdit} />
	<Readiness settings={current} />
	<Rules />
	<IssuerForm settings={data.settings!} countries={data.countries} {canEdit} onsaved={(s) => (saved = s)} />
{/if}
