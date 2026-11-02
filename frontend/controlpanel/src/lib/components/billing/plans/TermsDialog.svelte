<script lang="ts">
	import type { BillingPlan } from '#lib/billing.ts';
	import Modal from '#lib/components/Modal.svelte';
	import TermsForm from './TermsForm.svelte';

	let { plan = $bindable(null), ondone }: { plan: BillingPlan | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	$effect(() => {
		open = !!plan;
	});
</script>

<Modal bind:open title={plan ? `Terms · ${plan.plan.name}` : 'Terms'} onclose={() => (plan = null)}>
	{#if plan}<TermsForm {plan} onclose={() => (open = false)} {ondone} />{/if}
</Modal>
