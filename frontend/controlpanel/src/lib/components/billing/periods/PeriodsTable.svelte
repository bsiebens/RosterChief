<script lang="ts">
	import { ChevronDown, ChevronRight } from '@lucide/svelte';
	import type { Due } from '#lib/billing.ts';
	import PeriodDetail from './PeriodDetail.svelte';
	import { day, money2, stateLabel, stateTone, statusLabel, vatPct } from './format.ts';

	let { dues, canIssue, onchange }: { dues: Due[]; canIssue: boolean; onchange: () => void } = $props();

	let openId = $state<string | null>(null);
</script>

<div class="overflow-x-auto">
	<table class="table">
		<thead>
			<tr>
				<th><span class="sr-only">Details</span></th>
				<th>Club</th>
				<th>Period</th>
				<th class="text-right">Net</th>
				<th class="text-right">VAT</th>
				<th class="text-right">Total</th>
				<th class="text-right">Paid</th>
				<th class="text-right">Balance</th>
				<th>Grace until</th>
				<th>Status</th>
				<th>Invoice</th>
			</tr>
		</thead>
		<tbody>
			{#each dues as due (due.id)}
				{@const isOpen = openId === due.id}
				<tr class={isOpen ? 'bg-paper' : ''}>
					<td class="w-8">
						<button class="btn btn-ghost btn-xs btn-square" aria-expanded={isOpen} aria-controls="period-{due.id}" aria-label="{isOpen ? 'Hide' : 'Show'} details for {due.club.name}, {day(due.period_start)}" onclick={() => (openId = isOpen ? null : due.id)}>
							{#if isOpen}<ChevronDown size={14} aria-hidden="true" />{:else}<ChevronRight size={14} aria-hidden="true" />{/if}
						</button>
					</td>
					<td>
						<a class="block font-semibold text-ink hover:underline" href="/clubs/{due.club.id}">{due.club.name}</a>
						<span class="block font-mono text-[11px] text-muted">{due.plan.name}</span>
					</td>
					<td class="font-mono text-xs whitespace-nowrap">
						{day(due.period_start)} – {day(due.period_end)}
						{#if due.is_trial}<span class="badge badge-info badge-xs ml-1">trial</span>{/if}
						{#if due.prorated}<span class="badge badge-warning badge-xs ml-1" title="Shortened by a plan change; the original period ended {day(due.original_period_end)}">prorated</span>{/if}
						{#if due.prorated && due.original_total}<span class="block text-[10px] text-muted">was {money2(due.original_total)}</span>{/if}
					</td>
					<td class="text-right font-mono tabular-nums">{money2(due.amount)}</td>
					<td class="text-right font-mono whitespace-nowrap tabular-nums">
						{money2(due.vat_amount)}
						<span class="block text-[10px] text-muted">{#if due.reverse_charge}<span class="badge badge-ghost badge-xs">reverse charge</span>{:else}VAT {vatPct(due.vat_rate)}{/if}</span>
					</td>
					<td class="text-right font-mono tabular-nums">{money2(due.total)}</td>
					<td class="text-right font-mono tabular-nums text-muted">{money2(due.amount_paid)}</td>
					<td class="text-right font-mono tabular-nums {due.status === 'cancelled' ? 'text-muted' : Number(due.balance) ? (due.state === 'overdue' ? 'font-bold text-club-dark' : 'text-ink') : 'text-muted'}">{due.status === 'cancelled' ? '—' : money2(due.balance)}</td>
					<td class="font-mono text-xs whitespace-nowrap {due.state === 'overdue' ? 'text-club-dark' : 'text-muted'}">{day(due.grace_until)}</td>
					<td class="whitespace-nowrap">
						{#if due.status === 'cancelled'}
							<span class="badge badge-sm badge-ghost">cancelled</span>
						{:else}
							<span class="badge badge-sm {stateTone[due.state]}">{stateLabel[due.state]}</span>
							<span class="ml-1 font-mono text-[11px] text-muted">{statusLabel[due.status]}</span>
						{/if}
					</td>
					<td class="whitespace-nowrap">
						{#if due.invoice}
							<span class="font-mono text-xs">{due.invoice.number}</span>
							{#if due.invoice.custom_number}<span class="badge badge-ghost badge-xs ml-1">custom</span>{/if}
							{#if due.voided_invoices?.length}<span class="badge badge-warning badge-xs ml-1" title="Replaces {due.invoice.replaces_number ?? due.voided_invoices[0].number}">reissued</span>{/if}
							<span class="block text-[11px] {due.invoice.sent_at ? 'text-muted' : due.invoice.needs_sending ? 'text-club-dark' : 'text-muted'}">
								{due.invoice.sent_at ? 'sent' : due.invoice.needs_sending ? 'not sent' : 'nothing to send'}
							</span>
						{:else}
							<span class="text-xs text-muted">none</span>
						{/if}
					</td>
				</tr>
				{#if isOpen}
					<tr id="period-{due.id}" class="bg-paper">
						<td colspan="11" class="p-0"><PeriodDetail row={due} {canIssue} {onchange} /></td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>
</div>
