<script lang="ts">
	import { ExternalLink, FastForward } from '@lucide/svelte';
	import type { Cycle } from '#lib/billing.ts';
	import { day, relative } from './fmt.ts';

	let { cycles, canAct, onopen, autoInvoicing = true }: { cycles: Cycle[]; canAct: boolean; onopen: (c: Cycle) => void; autoInvoicing?: boolean } = $props();

	function status(c: Cycle): { label: string; tone: string; note?: string } {
		if (c.downgraded_at) return { label: 'Downgraded', tone: 'badge-warning', note: `on the free plan since ${day(c.downgraded_at.slice(0, 10))} — unpaid` };
		if (c.lapsed) return { label: 'Lapsed', tone: 'badge-error', note: 'period ended, next period opens automatically' };
		if (c.opened) return { label: 'Opened', tone: 'badge-success', note: 'next period already invoiced' };
		if (c.invoice_due_now) return autoInvoicing ? { label: 'Invoice due now', tone: 'badge-warning', note: 'opens with the next scheduled run, or open it now' } : { label: 'Needs invoice', tone: 'badge-warning', note: 'automatic invoicing is off: open the period and invoice it by hand' };
		return { label: 'Upcoming', tone: '' };
	}
</script>

<div class="card overflow-x-auto">
	<table class="table">
		<thead>
			<tr>
				<th>Status</th>
				<th>Club</th>
				<th>Plan</th>
				<th>Current period ends</th>
				<th>Next period starts</th>
				<th>Invoice opens</th>
				<th class="text-right">Actions</th>
			</tr>
		</thead>
		<tbody>
			{#each cycles as c (c.club.id)}
				{@const s = status(c)}
				<tr class={c.invoice_due_now && !c.opened ? 'bg-row-warn' : ''}>
					<td>
						<span class="badge badge-sm {s.tone}">{s.label}</span>
						{#if s.note}<div class="mt-1 max-w-48 font-mono text-[11px] text-muted">{s.note}</div>{/if}
					</td>
					<td>
						<a class="link font-semibold text-ink" href="/clubs/{c.club.id}">{c.club.name}</a>
						<div class="font-mono text-[11px] text-muted">{c.club.slug}</div>
					</td>
					<td>
						<span class="text-sm">{c.plan.name}</span>
						{#if c.trial_ends_at}<div class="font-mono text-[11px] text-muted">trial until {day(c.trial_ends_at)} · then billed on {c.plan.name}</div>{/if}
					</td>
					<td class="font-mono text-xs whitespace-nowrap">{c.current_period_end ? day(c.current_period_end) : 'no period started'}</td>
					<td class="font-mono text-xs whitespace-nowrap">{day(c.next_period_start)}<div class="text-muted">{relative(c.next_period_start)}</div></td>
					<td class="font-mono text-xs whitespace-nowrap">{day(c.invoice_opens_on)}<div class={c.invoice_due_now && !c.opened ? 'text-warn-text' : 'text-muted'}>{relative(c.invoice_opens_on)} · {c.renewal_lead_days} d ahead</div></td>
					<td>
						<div class="flex items-center justify-end gap-2">
							{#if canAct}
								<button class="btn btn-outline btn-sm" disabled={c.opened} onclick={() => onopen(c)} title={c.opened ? 'The next period is already open' : undefined}>
									<FastForward size={13} aria-hidden="true" /> {autoInvoicing ? 'Open next period now' : 'Open period (no invoice)'}<span class="sr-only"> for {c.club.name}</span>
								</button>
							{/if}
							<a class="btn btn-outline btn-sm btn-square" href="/clubs/{c.club.id}" aria-label="Open club {c.club.name}" title="Open club"><ExternalLink size={13} aria-hidden="true" /></a>
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
