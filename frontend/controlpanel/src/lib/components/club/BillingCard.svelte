<script lang="ts">
	import { ExternalLink, Receipt } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { CreditLedger } from '#lib/billing.ts';
	import type { ClubBilling, ClubDetail } from '#lib/clubs.ts';
	import CreditNotePdf from '../billing/credit/CreditNotePdf.svelte';
	import Modal from '../Modal.svelte';
	import { money } from '#lib/dashboard.ts';
	import { dateTime, day, money2 } from '#lib/components/billing/periods/format.ts';
	import Alert from '../Alert.svelte';

	let { club, billing, canEdit, onchange, archiveDays = 30 }: { club: ClubDetail; billing: ClubBilling | null; canEdit: boolean; onchange: () => void; archiveDays?: number } = $props();

	let busy = $state(false);
	let error = $state('');
	let notes = $state('');
	$effect(() => {
		notes = billing?.notes ?? '';
	});

	async function patch(body: Record<string, unknown>) {
		busy = true;
		error = '';
		const r = await platform('PATCH', `/clubs/${club.id}/billing`, body);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The billing settings could not be saved.');
		onchange();
	}

	let ledgerOpen = $state(false);
	let ledger = $state<CreditLedger | null>(null);
	let ledgerError = $state('');
	const KIND: Record<string, string> = { overpayment: 'Paid more than the shortened period', cancelled_period: 'Cancelled period', applied: 'Applied to an invoice' };

	async function openLedger() {
		ledger = null;
		ledgerError = '';
		ledgerOpen = true;
		const r = await platform<CreditLedger>('GET', `/clubs/${club.id}/billing/credit`);
		if (r.ok && r.data) ledger = r.data;
		else ledgerError = r.message ?? 'The credit ledger could not be loaded.';
	}

	const date = (d: string) => new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
	const stateTone = { overdue: 'badge-error', in_grace: 'badge-warning', issued_ahead: 'badge-info', settled: 'badge-success' } as const;
	const stateLabel = { overdue: 'overdue', in_grace: 'in grace', issued_ahead: 'upcoming', settled: 'settled' } as const;
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title"><Receipt size={16} aria-hidden="true" /> Billing</div>
		{#if error}<Alert kind="error">{error}</Alert>{/if}

		{#if !billing || !billing.billed}
			<p class="text-sm">This club is <strong>not billed</strong>: it uses the platform for free. Billing is started from the <a class="link font-semibold" href="/billing/cycles">billing cycles</a> page.</p>
		{:else}
			<div class="flex flex-wrap items-end gap-x-8 gap-y-2">
				<div>
					<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">billed plan</div>
					<div class="font-display text-xl leading-none font-extrabold text-ink uppercase">{billing.plan?.name ?? '—'}</div>
				</div>
				<div>
					<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">owed</div>
					<div class="font-display text-xl leading-none font-extrabold tabular-nums {Number(billing.owed) ? 'text-club-dark' : 'text-ink'}">{money(billing.owed)}</div>
				</div>
			</div>

			<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
				<div>
					<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">club credit</div>
					<div class="font-display text-xl leading-none font-extrabold tabular-nums {Number(billing.credit_balance) > 0 ? 'text-ok-text' : 'text-ink'}">{money2(billing.credit_balance)}</div>
				</div>
				<button class="btn btn-outline btn-xs" onclick={openLedger}>Ledger</button>
				<span class="text-xs text-muted">Applied automatically to the next invoices; never refunded by the system.</span>
			</div>

			{#if billing.trial_ends_at}
				<p class="text-sm">Trial until <strong>{date(billing.trial_ends_at)}</strong>, then billed automatically.</p>
			{/if}
			{#if billing.downgraded_at}
				<Alert kind="warning">Downgraded to the free plan since {date(billing.downgraded_at)} because of unpaid periods. Paying them restores the plan; archiving follows after {archiveDays} days if auto-archive is on.</Alert>
			{/if}

			<div class="flex flex-wrap gap-x-6 gap-y-2 text-sm">
				<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" class="checkbox" checked={billing.auto_renew ?? false} disabled={!canEdit || busy} onchange={(e) => patch({ auto_renew: e.currentTarget.checked })} /> Renew automatically</label>
				<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" class="checkbox" checked={billing.auto_archive ?? false} disabled={!canEdit || busy} onchange={(e) => patch({ auto_archive: e.currentTarget.checked })} /> Archive when overdue</label>
			</div>
			<p class="text-xs text-muted">Archive when overdue: when a period stays unpaid past its grace the club is first moved to the free plan, and archived {archiveDays} days later. Downgrading happens regardless.</p>

			<div class="form-control">
				<label class="label" for="billing-notes"><span class="label-text">Notes</span></label>
				<textarea id="billing-notes" class="textarea textarea-bordered w-full" rows="2" disabled={!canEdit} bind:value={notes}></textarea>
				{#if canEdit && notes !== billing.notes}
					<div class="mt-2"><button class="btn btn-outline btn-xs" disabled={busy} onclick={() => patch({ notes })}>Save notes</button></div>
				{/if}
			</div>

			{#if billing.credit_notes?.length}
				<div>
					<div class="mb-1 font-mono text-[10px] tracking-[.08em] text-muted uppercase">latest credit notes</div>
					<ul class="divide-y divide-rule rounded border border-rule text-xs">
						{#each billing.credit_notes as n (n.id)}
							<li class="flex items-center justify-between gap-2 px-3 py-1.5">
								<span class="flex items-center gap-2"><CreditNotePdf id={n.id} number={n.number} /><span class="text-muted">{day(n.issued_at)}</span></span>
								<span class="font-mono tabular-nums">{money2(n.total)}</span>
							</li>
						{/each}
					</ul>
					<a class="link mt-1 inline-block text-xs" href="/billing/credit-notes?club={club.id}">All credit notes</a>
				</div>
			{/if}

			{#if billing.dues.length}
				<ul class="-mx-4 mt-1 divide-y divide-rule border-t border-rule">
					{#each billing.dues.slice(0, 6) as due (due.id)}
						{@const closed = due.status === 'waived' || due.status === 'cancelled'}
						<li class="flex flex-col gap-1 px-4 py-2.5">
							<div class="flex items-center justify-between gap-2">
								<span class="font-mono text-xs whitespace-nowrap">{date(due.period_start)} – {date(due.period_end)}{#if due.is_trial}<span class="badge badge-info badge-xs ml-1">trial</span>{/if}{#if due.prorated}<span class="badge badge-warning badge-xs ml-1" title="Shortened by a plan change">prorated</span>{/if}</span>
								{#if closed}<span class="badge badge-sm badge-ghost">{due.status}</span>
								{:else if due.status === 'paid'}<span class="badge badge-sm badge-success">{Number(due.total) === 0 ? 'settled · nothing to pay' : 'paid'}</span>
								{:else}<span class="badge badge-sm {stateTone[due.state]}">{stateLabel[due.state]}</span>{/if}
							</div>
							<div class="flex items-center justify-between gap-2 font-mono text-xs">
								{#if due.invoice}
									<span class="inline-flex items-center gap-1.5">
										<a class="link font-semibold" href="/api/platform/v1/billing/dues/{due.id}/invoice.pdf" target="_blank" rel="noopener noreferrer" title="Open the invoice PDF">{due.invoice.number}</a>
										<a class="inline-flex items-center text-dim hover:text-ink" href="/billing?status=all&q={encodeURIComponent(due.invoice.number)}" title="Open this period in Billing" aria-label="Open {due.invoice.number} in Billing"><ExternalLink size={12} aria-hidden="true" /></a>
									</span>
								{:else}<span class="text-dim">no invoice</span>{/if}
								<span class="tabular-nums text-muted">
									{#if due.prorated && due.original_total}<span class="text-dim">was {money2(due.original_total)} · </span>{/if}{money2(due.total)}
									{#if Number(due.balance) && !closed}<span class="text-club-dark"> · {money2(due.balance)} open</span>{/if}
								</span>
							</div>
						</li>
					{/each}
				</ul>
				{#if billing.dues.length > 6}<p class="font-mono text-[11px] text-muted">Showing the latest 6 of {billing.dues.length} periods.</p>{/if}
			{/if}
		{/if}
	</div>
</div>

<Modal bind:open={ledgerOpen} title="Club credit ledger" wide>
	{#if ledgerError}<Alert kind="error">{ledgerError}</Alert>
	{:else if !ledger}<p class="text-sm text-muted">Loading…</p>
	{:else}
		<p class="mb-3 text-sm">Balance: <strong class="font-mono tabular-nums {Number(ledger.balance) > 0 ? 'text-ok-text' : ''}">{money2(ledger.balance)}</strong>. Credit is applied automatically to new invoices and carried forward; it is never refunded by the system.</p>
		{#if ledger.entries.length}
			<div class="overflow-x-auto">
				<table class="table text-sm">
					<thead><tr><th>Date</th><th>What</th><th>Note</th><th class="text-right">Amount</th></tr></thead>
					<tbody>
						{#each ledger.entries as e (e.id)}
							<tr>
								<td class="font-mono text-xs whitespace-nowrap">{dateTime(e.created)}</td>
								<td>{KIND[e.kind] ?? e.kind}{#if e.credit_note_number}<span class="block font-mono text-[11px] text-muted">{e.credit_note_number}</span>{/if}</td>
								<td class="text-xs text-muted">{e.note}</td>
								<td class="text-right font-mono whitespace-nowrap tabular-nums {Number(e.amount) > 0 ? 'text-ok-text' : 'text-ink'}">{Number(e.amount) > 0 ? '+' : ''}{money2(e.amount)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="text-sm text-muted">No credit movements yet.</p>
		{/if}
	{/if}
	<div class="modal-action"><button class="btn btn-outline" onclick={() => (ledgerOpen = false)}>Close</button></div>
</Modal>
