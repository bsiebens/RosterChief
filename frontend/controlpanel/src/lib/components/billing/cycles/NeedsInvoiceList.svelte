<script lang="ts">
	import { FilePlus, FastForward } from '@lucide/svelte';
	import type { InvoicingNeed } from '#lib/billing.ts';
	import { day, price } from './fmt.ts';

	/** Clubs that need an invoice by hand (automatic invoicing is off): the period is not opened yet, or it was opened without an invoice. */
	let { items, canAct, onopen, oninvoice }: { items: InvoicingNeed[]; canAct: boolean; onopen: (n: InvoicingNeed) => void; oninvoice: (n: InvoicingNeed) => void } = $props();

	const when = (n: InvoicingNeed) => (n.days < 0 ? `window opened ${-n.days} day${n.days === -1 ? '' : 's'} ago` : n.days === 0 ? 'window opens today' : `window opens in ${n.days} day${n.days === 1 ? '' : 's'}`);
</script>

<div class="card overflow-x-auto">
	<table class="table">
		<thead>
			<tr>
				<th>Status</th>
				<th>Club</th>
				<th>Plan</th>
				<th>Period starts</th>
				<th class="text-right">Amount</th>
				<th class="text-right">Actions</th>
			</tr>
		</thead>
		<tbody>
			{#each items as n (n.club.id + n.period_start)}
				<tr class={n.days <= 0 ? 'bg-row-warn' : ''}>
					<td>
						<span class="badge badge-sm {n.days <= 0 ? 'badge-warning' : ''}">{n.kind === 'invoice' ? 'Needs invoice' : 'Open and invoice'}</span>
						<div class="mt-1 font-mono text-[11px] {n.days <= 0 ? 'text-warn-text' : 'text-muted'}">{when(n)}</div>
					</td>
					<td>
						<a class="link font-semibold text-ink" href="/clubs/{n.club.id}">{n.club.name}</a>
						<div class="font-mono text-[11px] text-muted">{n.club.slug}</div>
					</td>
					<td class="text-sm">{n.plan.name}</td>
					<td class="font-mono text-xs whitespace-nowrap">{day(n.period_start)}</td>
					<td class="text-right font-mono text-xs whitespace-nowrap">{n.amount ? price(n.amount) : '—'}</td>
					<td>
						<div class="flex items-center justify-end gap-2">
							{#if canAct}
								{#if n.kind === 'open'}
									<button class="btn btn-outline btn-sm" onclick={() => onopen(n)}><FastForward size={13} aria-hidden="true" /> Open period<span class="sr-only"> for {n.club.name}</span></button>
								{:else}
									<button class="btn btn-primary btn-sm" onclick={() => oninvoice(n)}><FilePlus size={13} aria-hidden="true" /> Create invoice<span class="sr-only"> for {n.club.name}</span></button>
								{/if}
							{/if}
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
