<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { Due } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { METHODS, money2, todayLocal } from './format.ts';

	let { due, ondone, onclose }: { due: Due; ondone: () => void; onclose: () => void } = $props();

	let open = $state(true);
	let amount = $state(untrack(() => due.balance));
	let method = $state('bank_transfer');
	let reference = $state('');
	let paidOn = $state(todayLocal());
	let note = $state('');
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let key = crypto.randomUUID();
	const methodId = $props.id();

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		fields = {};
		// Today keeps the current time; another day is recorded at noon so it cannot slip across a date line.
		const paid_at = paidOn === todayLocal() ? new Date().toISOString() : new Date(`${paidOn}T12:00:00`).toISOString();
		const r = await platform('POST', `/billing/dues/${due.id}/payments`, { amount: amount.trim(), method, reference: reference.trim(), paid_at, note: note.trim() }, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (r.ok) {
			ondone();
			open = false;
			return;
		}
		if (r.status !== 0) key = crypto.randomUUID(); // a rejected attempt is not retried as-is
		fields = r.fields;
		error = r.message ?? 'The payment could not be recorded.';
	}
</script>

<Modal bind:open title="Record payment" {onclose}>
	<form class="flex flex-col gap-3" onsubmit={submit}>
		<p class="text-sm text-muted">
			{due.club.name}: open balance <strong class="text-ink">{money2(due.balance)}</strong> of {money2(due.total)} (incl. VAT). A payment cannot exceed the open balance.
		</p>
		<Field label="Amount (EUR)" type="text" inputmode="decimal" mono required bind:value={amount} error={fields.amount} />
		<div class="form-control w-full">
			<label class="label" for={methodId}><span class="label-text">Method</span></label>
			<select id={methodId} class="select select-bordered w-full" bind:value={method}>
				{#each METHODS as [value, label] (value)}<option {value}>{label}</option>{/each}
			</select>
			{#if fields.method}<span class="mt-1 block text-xs text-club-dark">{fields.method}</span>{/if}
		</div>
		<Field label="Reference (optional)" bind:value={reference} placeholder="Bank reference, receipt number…" maxlength={255} />
		<Field label="Date received" type="date" required max={todayLocal()} bind:value={paidOn} error={fields.paid_at} />
		<Field label="Note (optional)" bind:value={note} />
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>Cancel</button>
			<button class="btn btn-primary" disabled={busy || !amount.trim() || !paidOn}>{busy ? 'Recording…' : 'Record payment'}</button>
		</div>
	</form>
</Modal>
