<script lang="ts">
	import Modal from '#lib/components/Modal.svelte';
	import StartBillingForm from './StartBillingForm.svelte';
	import type { PlanLite, Unbilled } from './types.ts';

	let { target = $bindable(null), plans, archiveAfterDays, nextNumber = null, ondone }: { target: Unbilled | null; plans: PlanLite[]; archiveAfterDays: number; nextNumber?: string | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	$effect(() => {
		open = !!target;
	});
</script>

<Modal bind:open title="Start billing" onclose={() => (target = null)}>
	{#if target}
		<StartBillingForm club={target} {plans} {archiveAfterDays} {nextNumber} onclose={() => (open = false)} {ondone} />
	{/if}
</Modal>
