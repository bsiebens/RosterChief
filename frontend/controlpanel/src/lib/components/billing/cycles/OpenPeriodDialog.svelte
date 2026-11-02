<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { Cycle } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import InvoiceNumberField from '#lib/components/billing/InvoiceNumberField.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { day, newKey } from './fmt.ts';

	let { cycle = $bindable(null), nextNumber = null, ondone }: { cycle: Cycle | null; nextNumber?: string | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	let busy = $state(false);
	let error = $state('');
	let key = '';
	let invoiceNumber = $state<string | null>(null);
	let numberError = $state('');

	// Setting `cycle` opens the dialog; it closes by itself when the cycle is cleared.
	$effect(() => {
		if (cycle) {
			error = numberError = '';
			invoiceNumber = null;
			key = newKey();
			open = true;
		} else open = false;
	});

	async function confirm() {
		if (!cycle) return;
		busy = true;
		error = '';
		const r = await platform('POST', `/clubs/${cycle.club.id}/billing/periods`, { start: cycle.next_period_start, invoice_number: invoiceNumber }, fetch, { 'Idempotency-Key': key });
		busy = false;
		numberError = r.fields.invoice_number ?? (!r.ok && r.status === 409 && invoiceNumber ? (r.message ?? '') : '');
		if (!r.ok) {
			if (r.status !== 0) key = newKey();
			return (error = numberError ? '' : (r.message ?? 'The period could not be opened.'));
		}
		const name = cycle.club.name;
		const start = cycle.next_period_start;
		cycle = null;
		ondone(`Opened the period starting ${day(start)} for ${name} and invoiced it.`);
	}
</script>

<Modal bind:open title="Open next period" onclose={() => (cycle = null)}>
	{#if cycle}
		<div class="flex flex-col gap-4">
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<p class="text-sm">
				Open and invoice the next period for <strong>{cycle.club.name}</strong> on <strong>{cycle.plan.name}</strong>, starting <strong class="font-mono">{day(cycle.next_period_start)}</strong>.
			</p>
			<p class="text-xs text-muted">
				The invoice is created with the plan price and VAT rate valid on that start date. {#if cycle.trial_ends_at}The club is on a free trial until {day(cycle.trial_ends_at)}; the first paid period normally opens by itself before it ends.{/if} It is not emailed automatically.
			</p>
			<InvoiceNumberField bind:value={invoiceNumber} nextAutomatic={nextNumber} error={numberError} disabled={busy} />
			<div class="modal-action mt-0">
				<button class="btn btn-ghost btn-sm" onclick={() => (open = false)} disabled={busy}>Cancel</button>
				<button class="btn btn-primary btn-sm" onclick={confirm} disabled={busy}>{busy ? 'Opening…' : 'Open and invoice'}</button>
			</div>
		</div>
	{/if}
</Modal>
