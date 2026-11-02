<script lang="ts">
	import { onMount } from 'svelte';
	import { Ban, HandCoins, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { Due, Payment } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import InvoiceActions from './InvoiceActions.svelte';
	import PaymentDialog from './PaymentDialog.svelte';
	import { dateTime, day, methodLabel, money2, owing } from './format.ts';

	let { row, canIssue, onchange }: { row: Due; canIssue: boolean; onchange: () => void } = $props();

	// The list rows carry no payments, so the full period is fetched when a row is opened.
	let full = $state<Due | null>(null);
	let loadError = $state('');
	const due = $derived(full ?? row);
	let dialog = $state<'' | 'pay' | 'waive'>('');
	let removing = $state<Payment | null>(null);

	async function load() {
		const r = await platform<Due>('GET', `/billing/dues/${row.id}`);
		if (r.ok && r.data) {
			full = r.data;
			loadError = '';
		} else loadError = r.message ?? 'The period could not be loaded.';
	}
	onMount(load);

	// After any change: reload this period and let the page refresh its list and totals.
	const done = () => {
		load();
		onchange();
	};

	const call = (method: string, path: string, fail: string) => async () => {
		const r = await platform(method, path);
		if (!r.ok) return r.message ?? fail;
		done();
	};
	// Credit payments are system-made, and payments that were moved to club credit (or sit on a cancelled period) are locked.
	const removable = (p: Payment) => p.method !== 'credit' && due.status !== 'cancelled' && !Number(due.credit_transferred);
	const canWaive = $derived(due.status === 'unpaid' && !!full && full.payments.length === 0);
</script>

<div class="grid gap-6 p-4 lg:grid-cols-2">
	{#if loadError}<div class="lg:col-span-2"><Alert kind="error">{loadError}</Alert></div>{/if}

	<div class="flex flex-col gap-2">
		{#if due.prorated || due.status === 'cancelled' || due.credit_notes?.length}
			<div class="rounded border border-rule bg-white px-3 py-2 text-sm">
				{#if due.prorated}
					<p>
						<span class="badge badge-warning badge-xs mr-1">prorated</span>Shortened by a plan change{due.prorated_at ? ` on ${dateTime(due.prorated_at)}` : ''}. Originally {day(due.period_start)} – {day(due.original_period_end)}:
						<span class="font-mono tabular-nums">{money2(due.original_amount)} + {money2(due.original_vat_amount)} VAT = {money2(due.original_total)}</span>, now <span class="font-mono tabular-nums">{money2(due.amount)} + {money2(due.vat_amount)} VAT = {money2(due.total)}</span>.
					</p>
				{/if}
				{#if due.status === 'cancelled'}
					<p><span class="badge badge-ghost badge-xs mr-1">cancelled</span>Cancelled by a plan change before it started. The invoice is kept, a credit note offsets it, and nothing is owed.</p>
				{/if}
				{#if Number(due.credit_transferred)}
					<p class="text-muted">{money2(due.credit_transferred)} of the payments was moved to the club's credit.</p>
				{/if}
				{#if due.credit_notes?.length}
					<p class="text-muted">Credit notes: {#each due.credit_notes as cn, i (cn.number)}{i ? ', ' : ''}<a class="link font-mono" href="/billing/credit-notes?q={encodeURIComponent(cn.number)}">{cn.number}</a> ({money2(cn.total)}){/each}</p>
				{/if}
			</div>
		{/if}
		<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">payments</div>
		{#if !full && !loadError}
			<p class="text-sm text-muted">Loading…</p>
		{:else if full}
			{#if full.payments.length}
				<ul class="divide-y divide-rule rounded border border-rule bg-white text-sm">
					{#each full.payments as p (p.id)}
						<li class="flex items-start justify-between gap-3 px-3 py-2">
							<div class="min-w-0">
								<div class="font-mono tabular-nums text-ink">{money2(p.amount)} <span class="font-sans text-muted">· {methodLabel(p.method)} · {dateTime(p.paid_at)}</span></div>
								{#if p.reference || p.note}<div class="truncate text-xs text-muted">{[p.reference, p.note].filter(Boolean).join(' · ')}</div>{/if}
								{#if p.recorded_by_email}<div class="text-xs text-dim">recorded by {p.recorded_by_email}</div>{/if}
							</div>
							{#if removable(p)}
								<button class="btn btn-ghost btn-xs text-club-dark hover:bg-danger-bg" onclick={() => (removing = p)} aria-label="Remove payment of {money2(p.amount)}"><Trash2 size={13} aria-hidden="true" /></button>
							{/if}
						</li>
					{/each}
				</ul>
			{:else}
				<p class="text-sm text-muted">No payments recorded.</p>
			{/if}
		{/if}
		<div class="flex flex-wrap gap-2 pt-1">
			{#if owing(due.status)}
				<button class="btn btn-primary btn-sm" onclick={() => (dialog = 'pay')}><HandCoins size={13} aria-hidden="true" /> Record payment</button>
			{/if}
			{#if canWaive}
				<button class="btn btn-outline btn-error btn-sm" onclick={() => (dialog = 'waive')}><Ban size={13} aria-hidden="true" /> Waive</button>
			{/if}
		</div>
	</div>

	<InvoiceActions {due} {canIssue} ondone={done} />
</div>

{#if dialog === 'pay'}
	<PaymentDialog {due} ondone={done} onclose={() => (dialog = '')} />
{:else if dialog === 'waive'}
	<ConfirmDialog title="Waive this period" confirmLabel="Waive period" danger onconfirm={call('POST', `/billing/dues/${due.id}/waive`, 'The period could not be waived.')} onclose={() => (dialog = '')}>
		This forgives the <strong>whole</strong> period of {due.club.name} ({money2(due.total)} incl. VAT): nothing will be owed for it. It is only possible while nothing has been paid.
	</ConfirmDialog>
{/if}
{#if removing}
	{@const p = removing}
	<ConfirmDialog title="Remove payment" confirmLabel="Remove payment" danger onconfirm={call('DELETE', `/billing/dues/${due.id}/payments/${p.id}`, 'The payment could not be removed.')} onclose={() => (removing = null)}>
		Remove the payment of <strong>{money2(p.amount)}</strong> ({methodLabel(p.method)}, {dateTime(p.paid_at)})? The period is re-settled and may become owing again.
	</ConfirmDialog>
{/if}
