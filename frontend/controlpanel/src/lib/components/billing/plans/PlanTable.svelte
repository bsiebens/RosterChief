<script lang="ts">
	import { ChevronDown, ChevronRight, Pencil, Plus } from '@lucide/svelte';
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import { day, plural, price } from '#lib/components/billing/cycles/fmt.ts';
	import MoveButtons from '#lib/components/MoveButtons.svelte';

	/**
	 * Plans and their billing cycles as one table, in the order the platform chose: a header row per plan (with its place in the order), then one
	 * row per billing cycle (with its place within the plan). The first active cycle of a plan is its default; price history opens under a cycle.
	 */
	let {
		plans,
		canEdit,
		busy = false,
		onmoveplan,
		onmovecycle,
		onaddcycle,
		oneditcycle,
		onprice
	}: {
		plans: BillingPlan[];
		canEdit: boolean;
		busy?: boolean;
		onmoveplan: (plan: BillingPlan, direction: -1 | 1) => void;
		onmovecycle: (plan: BillingPlan, cycle: PlanCycle, direction: -1 | 1) => void;
		onaddcycle: (plan: BillingPlan) => void;
		oneditcycle: (plan: BillingPlan, cycle: PlanCycle) => void;
		onprice: (plan: BillingPlan, cycle: PlanCycle) => void;
	} = $props();

	const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in the browser's zone
	let openHistory = $state<Record<string, boolean>>({});
	const history = (c: PlanCycle) => [...c.prices].sort((a, b) => b.active_from.localeCompare(a.active_from));
	const currentId = (c: PlanCycle) => history(c).find((p) => p.active_from <= today)?.id;
	const defaultId = (plan: BillingPlan) => plan.cycles.find((c) => c.is_active)?.id;
	const COLS = 8;
</script>

<div class="card overflow-x-auto">
	<table class="table">
		<caption class="sr-only">Plans in the platform's order, each with its billing cycles and net prices</caption>
		<thead>
			<tr>
				<th scope="col" class="w-24">Order</th>
				<th scope="col">Plan / billing cycle</th>
				<th scope="col" class="text-right">Period</th>
				<th scope="col" class="text-right">Invoiced before</th>
				<th scope="col" class="text-right">Grace</th>
				<th scope="col" class="text-right">Price now, net</th>
				<th scope="col" class="text-right">Clubs</th>
				<th scope="col"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		{#each plans as plan, i (plan.plan.id)}
			<tbody class="border-t-2 border-rule">
				<tr class="bg-subhead {plan.is_active ? '' : 'opacity-70'}">
					<td>{#if canEdit}<MoveButtons label="plan {plan.plan.name}" canUp={i > 0} canDown={i < plans.length - 1} {busy} onmove={(d) => onmoveplan(plan, d)} />{/if}</td>
					<th scope="rowgroup" colspan={COLS - 3} class="text-left">
						<span class="font-display text-base leading-none font-extrabold tracking-[.04em] text-ink uppercase">{plan.plan.name}</span>
						{#if !plan.is_active}<span class="badge badge-sm ml-2">inactive</span>{/if}
						<span class="ml-2 font-mono text-[11px] font-normal text-muted">{plan.plan.code}</span>
					</th>
					<td class="text-right font-mono text-xs text-muted">{plural(plan.club_count, 'billed club')}</td>
					<td class="text-right">{#if canEdit}<button class="btn btn-outline btn-xs" onclick={() => onaddcycle(plan)}><Plus size={12} aria-hidden="true" /> Cycle<span class="sr-only"> for {plan.plan.name}</span></button>{/if}</td>
				</tr>
				{#each plan.cycles as cycle, j (cycle.id)}
					<tr class={cycle.is_active ? '' : 'opacity-60'}>
						<td class="pl-8">{#if canEdit}<MoveButtons label="cycle {cycle.name} of {plan.plan.name}" canUp={j > 0} canDown={j < plan.cycles.length - 1} {busy} onmove={(d) => onmovecycle(plan, cycle, d)} />{/if}</td>
						<td>
							<span class="font-semibold text-ink">{cycle.name}</span>
							{#if cycle.id === defaultId(plan)}<span class="badge badge-info badge-xs ml-1.5">default</span>{/if}
							{#if !cycle.is_active}<span class="badge badge-xs ml-1.5">inactive</span>{/if}
						</td>
						<td class="text-right text-sm whitespace-nowrap">{plural(cycle.duration_months, 'month')}</td>
						<td class="text-right text-sm whitespace-nowrap">{plural(cycle.renewal_lead_days, 'day')}</td>
						<td class="text-right text-sm whitespace-nowrap">{plural(cycle.grace_days, 'day')}</td>
						<td class="text-right font-mono tabular-nums">
							{#if cycle.current_price == null}<span class="text-xs text-warn-text">no price yet</span>{:else}<strong class="text-ink">€ {price(cycle.current_price)}</strong>{/if}
						</td>
						<td class="text-right font-mono text-xs text-muted">{cycle.club_count}</td>
						<td class="text-right whitespace-nowrap">
							<button class="btn btn-ghost btn-xs" aria-expanded={!!openHistory[cycle.id]} onclick={() => (openHistory[cycle.id] = !openHistory[cycle.id])} disabled={!cycle.prices.length}>
								{#if openHistory[cycle.id]}<ChevronDown size={13} aria-hidden="true" />{:else}<ChevronRight size={13} aria-hidden="true" />{/if} History ({cycle.prices.length})<span class="sr-only"> of {cycle.name} on {plan.plan.name}</span>
							</button>
							{#if canEdit}
								<button class="btn btn-outline btn-xs" onclick={() => onprice(plan, cycle)}><Plus size={12} aria-hidden="true" /> Price<span class="sr-only"> for {cycle.name} of {plan.plan.name}</span></button>
								<button class="btn btn-outline btn-xs" onclick={() => oneditcycle(plan, cycle)}><Pencil size={12} aria-hidden="true" /> Edit<span class="sr-only"> {cycle.name} cycle of {plan.plan.name}</span></button>
							{/if}
						</td>
					</tr>
					{#if openHistory[cycle.id]}
						<tr>
							<td></td>
							<td colspan={COLS - 1} class="bg-paper">
								<table class="table table-sm w-full max-w-md">
									<caption class="sr-only">Dated prices of {cycle.name} on {plan.plan.name}</caption>
									<thead><tr><th>Valid from</th><th class="text-right">Net amount</th><th></th></tr></thead>
									<tbody>
										{#each history(cycle) as p (p.id)}
											<tr>
												<td class="font-mono text-xs">{day(p.active_from)}</td>
												<td class="text-right font-mono tabular-nums">€ {price(p.amount)}</td>
												<td class="text-right">
													{#if p.active_from > today}<span class="badge badge-warning badge-xs">scheduled</span>
													{:else if p.id === currentId(cycle)}<span class="badge badge-success badge-xs">current</span>
													{:else}<span class="font-mono text-[11px] text-dim">replaced</span>{/if}
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		{/each}
	</table>
</div>
