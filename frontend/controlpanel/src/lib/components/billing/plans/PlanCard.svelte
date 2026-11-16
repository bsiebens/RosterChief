<script lang="ts">
	import { Pencil, Plus } from '@lucide/svelte';
	import type { BillingPlan } from '#lib/billing.ts';
	import { day, plural, price } from '#lib/components/billing/cycles/fmt.ts';

	let { plan, canEdit, onterms, onprice }: { plan: BillingPlan; canEdit: boolean; onterms: () => void; onprice: () => void } = $props();

	const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in the browser's zone
	const t = $derived(plan.terms);
	const history = $derived([...plan.prices].sort((a, b) => b.active_from.localeCompare(a.active_from)));
	const currentId = $derived(history.find((p) => p.active_from <= today)?.id);
</script>

<div class="card {plan.is_active ? '' : 'opacity-70'}">
	<div class="card-body gap-3">
		<div class="flex flex-wrap items-start justify-between gap-2">
			<div class="min-w-0">
				<div class="card-title">{plan.plan.name}{#if !plan.is_active}<span class="badge badge-sm">inactive</span>{/if}</div>
				<div class="font-mono text-[11px] text-muted">{plan.plan.code} · {plural(plan.club_count, 'billed club')}</div>
			</div>
			<div class="text-right">
				<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">price now, excl. VAT</div>
				<div class="font-display text-2xl leading-none font-extrabold tabular-nums text-ink">{plan.current_price == null ? '—' : `€ ${price(plan.current_price)}`}</div>
				{#if plan.current_price == null}<div class="mt-1 text-xs text-warn-text">No price yet: clubs cannot be billed on this plan.</div>{/if}
			</div>
		</div>

		<div class="flex flex-wrap items-center justify-between gap-2 rounded border border-rule bg-subhead px-3 py-2 text-sm">
			{#if t}
				<span>{plural(t.duration_months, 'month')} · invoiced {plural(t.renewal_lead_days, 'day')} before · {plural(t.grace_days, 'day')} grace</span>
			{:else}
				<span class="text-muted">No terms set.</span>
			{/if}
			{#if canEdit}<button class="btn btn-outline btn-xs" onclick={onterms}><Pencil size={12} aria-hidden="true" /> Edit terms<span class="sr-only"> of {plan.plan.name}</span></button>{/if}
		</div>

		<div>
			<div class="mb-1 flex items-center justify-between">
				<h3 class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">Price history</h3>
				{#if canEdit}<button class="btn btn-outline btn-xs" onclick={onprice}><Plus size={12} aria-hidden="true" /> New price<span class="sr-only"> for {plan.plan.name}</span></button>{/if}
			</div>
			{#if history.length}
				<table class="table table-sm">
					<caption class="sr-only">Dated prices of {plan.plan.name}</caption>
					<thead><tr><th>Valid from</th><th class="text-right">Net amount</th><th></th></tr></thead>
					<tbody>
						{#each history as p (p.id)}
							<tr>
								<td class="font-mono text-xs">{day(p.active_from)}</td>
								<td class="text-right font-mono tabular-nums">€ {price(p.amount)}</td>
								<td class="text-right">
									{#if p.active_from > today}<span class="badge badge-warning badge-xs">scheduled</span>
									{:else if p.id === currentId}<span class="badge badge-success badge-xs">current</span>
									{:else}<span class="font-mono text-[11px] text-dim">replaced</span>{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="text-sm text-muted">No prices yet.</p>
			{/if}
		</div>
	</div>
</div>
