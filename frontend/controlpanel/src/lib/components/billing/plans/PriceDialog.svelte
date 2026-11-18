<script lang="ts">
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import Modal from '#lib/components/Modal.svelte';
	import PriceForm from './PriceForm.svelte';

	/** A new dated price for one billing cycle of a plan. */
	let { target = $bindable(null), ondone }: { target: { plan: BillingPlan; cycle: PlanCycle } | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	$effect(() => {
		open = !!target;
	});
</script>

<Modal bind:open title={target ? `New price · ${target.plan.plan.name} · ${target.cycle.name}` : 'New price'} onclose={() => (target = null)}>
	{#if target}<PriceForm plan={target.plan} cycle={target.cycle} onclose={() => (open = false)} {ondone} />{/if}
</Modal>
