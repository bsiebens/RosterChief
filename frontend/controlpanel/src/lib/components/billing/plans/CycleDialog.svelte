<script lang="ts">
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import Modal from '#lib/components/Modal.svelte';
	import CycleForm from './CycleForm.svelte';

	/** `target` is the plan to add a cycle to, or (with `cycle`) the cycle to edit. */
	let { target = $bindable(null), cycle = null, ondone }: { target: BillingPlan | null; cycle?: PlanCycle | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	$effect(() => {
		open = !!target;
	});
</script>

<Modal bind:open title={target ? (cycle ? `Billing cycle · ${cycle.name} · ${target.plan.name}` : `New billing cycle · ${target.plan.name}`) : 'Billing cycle'} onclose={() => (target = null)}>
	{#if target}{#key cycle?.id ?? 'new'}<CycleForm plan={target} {cycle} onclose={() => (open = false)} {ondone} />{/key}{/if}
</Modal>
