<script lang="ts">
	import { refreshAll } from '$app/navigation';
	import type { BillingPlan } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import PlanCard from '#lib/components/billing/plans/PlanCard.svelte';
	import PriceDialog from '#lib/components/billing/plans/PriceDialog.svelte';
	import TermsDialog from '#lib/components/billing/plans/TermsDialog.svelte';
	import VatNote from '#lib/components/billing/plans/VatNote.svelte';

	let { data } = $props();

	// The backend lets any signed-in staff member change terms and prices.
	const canEdit = $derived(data.auth.status === 'authenticated');
	let editTerms = $state<BillingPlan | null>(null);
	let newPrice = $state<BillingPlan | null>(null);
	let notice = $state('');

	function done(message: string) {
		notice = message;
		refreshAll();
	}
</script>

<svelte:head><title>Plans &amp; pricing · RosterChief</title></svelte:head>

<VatNote rate={data.vatRate} />

{#if notice}<Alert kind="success">{notice}</Alert>{/if}
{#if data.error}
	<Alert kind="error">{data.error}</Alert>
{:else if !data.plans.length}
	<div class="card"><p class="p-6 text-center text-sm text-muted">There are no plans yet.</p></div>
{:else}
	<div class="grid gap-4 lg:grid-cols-2">
		{#each data.plans as plan (plan.plan.id)}
			<PlanCard {plan} {canEdit} onterms={() => (editTerms = plan)} onprice={() => (newPrice = plan)} />
		{/each}
	</div>
{/if}

<TermsDialog bind:plan={editTerms} ondone={done} />
<PriceDialog bind:plan={newPrice} ondone={done} />
