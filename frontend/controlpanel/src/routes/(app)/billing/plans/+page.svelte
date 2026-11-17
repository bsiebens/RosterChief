<script lang="ts">
	import { refreshAll } from '$app/navigation';
	import { platform } from '#lib/api.ts';
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import CycleDialog from '#lib/components/billing/plans/CycleDialog.svelte';
	import PlanTable from '#lib/components/billing/plans/PlanTable.svelte';
	import PriceDialog from '#lib/components/billing/plans/PriceDialog.svelte';
	import VatNote from '#lib/components/billing/plans/VatNote.svelte';

	let { data } = $props();

	// The backend lets any signed-in staff member change cycles, prices and the order.
	const canEdit = $derived(data.auth.status === 'authenticated');
	let cycleTarget = $state<BillingPlan | null>(null);
	let cycleEdit = $state<PlanCycle | null>(null);
	let priceTarget = $state<{ plan: BillingPlan; cycle: PlanCycle } | null>(null);
	let notice = $state('');
	let error = $state('');
	let busy = $state(false);

	function done(message: string) {
		notice = message;
		error = '';
		refreshAll();
	}

	const swap = <T,>(list: T[], index: number, direction: -1 | 1) => {
		const next = [...list];
		[next[index], next[index + direction]] = [next[index + direction], next[index]];
		return next;
	};

	/** Plans are ordered by the platform; the same order shows in the feature lists and in every plan picker. */
	async function movePlan(plan: BillingPlan, direction: -1 | 1) {
		const index = data.plans.findIndex((p) => p.plan.id === plan.plan.id);
		if (index + direction < 0 || index + direction >= data.plans.length) return;
		busy = true;
		const r = await platform('PUT', '/plans/order', { plan_ids: swap(data.plans, index, direction).map((p) => p.plan.id) });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The order could not be saved.');
		done(`${plan.plan.name} moved ${direction < 0 ? 'up' : 'down'}.`);
	}

	async function moveCycle(plan: BillingPlan, cycle: PlanCycle, direction: -1 | 1) {
		const index = plan.cycles.findIndex((c) => c.id === cycle.id);
		if (index + direction < 0 || index + direction >= plan.cycles.length) return;
		busy = true;
		const r = await platform('PUT', `/billing/plans/${plan.plan.id}/cycles/order`, { cycle_ids: swap(plan.cycles, index, direction).map((c) => c.id) });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The order could not be saved.');
		done(`${cycle.name} of ${plan.plan.name} moved ${direction < 0 ? 'up' : 'down'}. The first active cycle is the default for new clubs.`);
	}
</script>

<svelte:head><title>Plans &amp; pricing · RosterChief</title></svelte:head>

<VatNote rate={data.vatRate} />

{#if notice}<Alert kind="success">{notice}</Alert>{/if}
{#if error}<Alert kind="error">{error}</Alert>{/if}
{#if data.error}
	<Alert kind="error">{data.error}</Alert>
{:else if !data.plans.length}
	<div class="card"><p class="p-6 text-center text-sm text-muted">There are no plans yet.</p></div>
{:else}
	<p class="text-sm text-muted">Each plan can be billed in several ways, each with its own price. Use the arrows to put plans and cycles in the order you want them listed and offered; the first active cycle is what a club gets when none is chosen.</p>
	<PlanTable
		plans={data.plans}
		{canEdit}
		{busy}
		onmoveplan={movePlan}
		onmovecycle={moveCycle}
		onaddcycle={(plan) => ((cycleEdit = null), (cycleTarget = plan))}
		oneditcycle={(plan, c) => ((cycleEdit = c), (cycleTarget = plan))}
		onprice={(plan, c) => (priceTarget = { plan, cycle: c })}
	/>
{/if}

<CycleDialog bind:target={cycleTarget} cycle={cycleEdit} ondone={done} />
<PriceDialog bind:target={priceTarget} ondone={done} />
