<script lang="ts">
	import { Pencil, Plus } from '@lucide/svelte';
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import { day, plural, price } from '#lib/components/billing/cycles/fmt.ts';
	import MoveButtons from '#lib/components/MoveButtons.svelte';

	let {
		plan,
		canEdit,
		index,
		total,
		busy = false,
		onmoveplan,
		onmovecycle,
		onaddcycle,
		oneditcycle,
		onprice
	}: {
		plan: BillingPlan;
		canEdit: boolean;
		/** Place of this plan in the platform's order (0 = first). */
		index: number;
		total: number;
		busy?: boolean;
		onmoveplan: (direction: -1 | 1) => void;
		onmovecycle: (cycle: PlanCycle, direction: -1 | 1) => void;
		onaddcycle: () => void;
		oneditcycle: (cycle: PlanCycle) => void;
		onprice: (cycle: PlanCycle) => void;
	} = $props();

	const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in the browser's zone
	const history = (c: PlanCycle) => [...c.prices].sort((a, b) => b.active_from.localeCompare(a.active_from));
	const currentId = (c: PlanCycle) => history(c).find((p) => p.active_from <= today)?.id;
	// The default is what a club gets when none is chosen: the first active cycle.
	const defaultId = $derived(plan.cycles.find((c) => c.is_active)?.id);
	const unpriced = $derived(plan.cycles.filter((c) => c.is_active && c.current_price == null).length);
</script>

<div class="card {plan.is_active ? '' : 'opacity-70'}">
	<div class="card-body gap-3">
		<div class="flex flex-wrap items-start justify-between gap-2">
			<div class="min-w-0">
				<div class="card-title">{plan.plan.name}{#if !plan.is_active}<span class="badge badge-sm">inactive</span>{/if}</div>
				<div class="font-mono text-[11px] text-muted">{plan.plan.code} · {plural(plan.club_count, 'billed club')}</div>
			</div>
			{#if canEdit}<MoveButtons label="plan {plan.plan.name}" canUp={index > 0} canDown={index < total - 1} {busy} onmove={onmoveplan} />{/if}
		</div>

		{#if unpriced}<div class="text-xs text-warn-text">{plural(unpriced, 'active cycle')} without a price: clubs cannot be billed on {unpriced === 1 ? 'it' : 'them'} yet.</div>{/if}

		<ul class="flex flex-col gap-3">
			{#each plan.cycles as cycle, i (cycle.id)}
				<li class="rounded border border-rule {cycle.is_active ? '' : 'opacity-70'}">
					<div class="flex flex-wrap items-start justify-between gap-2 border-b border-rule bg-subhead px-3 py-2">
						<div class="min-w-0">
							<div class="flex flex-wrap items-center gap-1.5">
								<span class="font-display text-base leading-none font-extrabold tracking-[.04em] text-ink uppercase">{cycle.name}</span>
								{#if cycle.id === defaultId}<span class="badge badge-info badge-xs">default</span>{/if}
								{#if !cycle.is_active}<span class="badge badge-xs">inactive</span>{/if}
							</div>
							<div class="mt-0.5 text-xs text-muted">{plural(cycle.duration_months, 'month')} · invoiced {plural(cycle.renewal_lead_days, 'day')} before · {plural(cycle.grace_days, 'day')} grace · {plural(cycle.club_count, 'club')}</div>
						</div>
						<div class="flex items-center gap-1">
							{#if canEdit}
								<MoveButtons label="cycle {cycle.name}" canUp={i > 0} canDown={i < plan.cycles.length - 1} {busy} onmove={(d) => onmovecycle(cycle, d)} />
								<button class="btn btn-outline btn-xs" onclick={() => oneditcycle(cycle)}><Pencil size={12} aria-hidden="true" /> Edit<span class="sr-only"> {cycle.name} cycle of {plan.plan.name}</span></button>
							{/if}
						</div>
					</div>
					<div class="flex flex-wrap items-end justify-between gap-2 px-3 py-2">
						<div>
							<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">price now, excl. VAT</div>
							<div class="font-display text-2xl leading-none font-extrabold tabular-nums text-ink">{cycle.current_price == null ? '—' : `€ ${price(cycle.current_price)}`}</div>
							{#if cycle.current_price == null}<div class="mt-1 text-xs text-warn-text">No price yet.</div>{/if}
						</div>
						{#if canEdit}<button class="btn btn-outline btn-xs" onclick={() => onprice(cycle)}><Plus size={12} aria-hidden="true" /> New price<span class="sr-only"> for {cycle.name} of {plan.plan.name}</span></button>{/if}
					</div>
					{#if cycle.prices.length}
						<details class="border-t border-rule px-3 py-1.5">
							<summary class="cursor-pointer font-mono text-[10px] tracking-[.08em] text-muted uppercase">Price history ({cycle.prices.length})</summary>
							<table class="table table-sm mt-1">
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
						</details>
					{/if}
				</li>
			{/each}
		</ul>

		{#if canEdit}<div><button class="btn btn-outline btn-sm" onclick={onaddcycle}><Plus size={13} aria-hidden="true" /> Add billing cycle<span class="sr-only"> to {plan.plan.name}</span></button></div>{/if}
	</div>
</div>
