<script lang="ts">
	import type { BillingPlan } from '#lib/billing.ts';
	import Modal from '#lib/components/Modal.svelte';
	import PriceForm from './PriceForm.svelte';

	let { plan = $bindable(null), ondone }: { plan: BillingPlan | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	$effect(() => {
		open = !!plan;
	});
</script>

<Modal bind:open title={plan ? `New price · ${plan.plan.name}` : 'New price'} onclose={() => (plan = null)}>
	{#if plan}<PriceForm {plan} onclose={() => (open = false)} {ondone} />{/if}
</Modal>
