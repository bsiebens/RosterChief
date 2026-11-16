<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { InvoicingNeed } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { day, newKey, price } from './fmt.ts';

	/** Create the invoice of a period that was opened without one. The number is typed by hand, never taken from the automatic sequence. */
	let { target = $bindable(null), ondone }: { target: InvoicingNeed | null; ondone: (message: string) => void } = $props();

	let open = $state(false);
	let busy = $state(false);
	let number = $state('');
	let error = $state('');
	let fieldError = $state('');
	let key = '';

	$effect(() => {
		if (target) {
			number = error = fieldError = '';
			key = newKey();
			open = true;
		} else open = false;
	});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!target?.due_id) return;
		busy = true;
		error = fieldError = '';
		const r = await platform('POST', `/billing/dues/${target.due_id}/invoice`, { number: number.trim() }, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (!r.ok) {
			if (r.status !== 0) key = newKey();
			if (r.fields.number) return (fieldError = r.fields.number);
			return (error = r.message ?? 'The invoice could not be created.');
		}
		const name = target.club.name;
		const created = number.trim();
		target = null;
		ondone(`Created invoice ${created} for ${name}.`);
	}
</script>

<Modal bind:open title="Create invoice" onclose={() => (target = null)}>
	{#if target}
		<form class="flex flex-col gap-3" onsubmit={submit}>
			<p class="text-sm">
				Create the invoice for <strong>{target.club.name}</strong>, period starting <strong class="font-mono">{day(target.period_start)}</strong>{#if target.amount} ({price(target.amount)} incl. VAT){/if}.
			</p>
			<Field label="Invoice number" required maxlength={32} bind:value={number} error={fieldError} help="Typed by hand: 1–32 characters, unique, and not in the automatic format set on the Invoicing tab. The automatic sequence is not used." />
			<Alert kind="warning" title="Numbers are permanent">An invoice number can never be changed once created. To correct a wrong invoice use Void and reissue on the period.</Alert>
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="flex justify-end gap-2">
				<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>Cancel</button>
				<button class="btn btn-primary" disabled={busy || !number.trim()}>{busy ? 'Creating…' : 'Create invoice'}</button>
			</div>
		</form>
	{/if}
</Modal>
