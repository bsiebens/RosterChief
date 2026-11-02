<script lang="ts">
	import { Receipt } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubBilling, ClubDetail } from '#lib/clubs.ts';
	import { money } from '#lib/dashboard.ts';
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

			{#if billing.dues.length}
				<div class="-mx-4 mt-1 overflow-x-auto border-t border-rule">
					<table class="table">
						<thead>
							<tr><th>Period</th><th class="text-right">Amount</th><th class="text-right">Balance</th><th>Status</th></tr>
						</thead>
						<tbody>
							{#each billing.dues.slice(0, 6) as due (due.id)}
								<tr>
									<td class="font-mono text-xs whitespace-nowrap">{date(due.period_start)} – {date(due.period_end)}{#if due.is_trial}<span class="badge badge-info badge-xs ml-1">trial</span>{/if}</td>
									<td class="text-right font-mono tabular-nums">{money(due.amount)}</td>
									<td class="text-right font-mono tabular-nums {Number(due.balance) ? 'text-club-dark' : 'text-muted'}">{money(due.balance)}</td>
									<td><span class="badge badge-sm {stateTone[due.state]}">{stateLabel[due.state]}</span></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if billing.dues.length > 6}<p class="font-mono text-[11px] text-muted">Showing the latest 6 of {billing.dues.length} periods.</p>{/if}
			{/if}
		{/if}
	</div>
</div>
