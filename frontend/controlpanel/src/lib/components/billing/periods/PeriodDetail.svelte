<script lang="ts">
	import { onMount } from 'svelte';
	import { Ban, HandCoins, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { Due, Payment } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import InvoiceActions from './InvoiceActions.svelte';
	import PaymentDialog from './PaymentDialog.svelte';
	import { dateTime, methodLabel, money2, owing } from './format.ts';

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
	const canWaive = $derived(due.status === 'unpaid' && !!full && full.payments.length === 0);
</script>

<div class="grid gap-6 p-4 lg:grid-cols-2">
	{#if loadError}<div class="lg:col-span-2"><Alert kind="error">{loadError}</Alert></div>{/if}

	<div class="flex flex-col gap-2">
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
							<button class="btn btn-ghost btn-xs text-club-dark hover:bg-danger-bg" onclick={() => (removing = p)} aria-label="Remove payment of {money2(p.amount)}"><Trash2 size={13} aria-hidden="true" /></button>
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
