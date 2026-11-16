<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { Due, VoidReissueResult } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { money2 } from './format.ts';

	let { due, ondone, onclose }: { due: Due; ondone: (result: VoidReissueResult) => void; onclose: () => void } = $props();

	const MAX_REASON = 300;
	let open = $state(true);
	let reason = $state('');
	let custom = $state(false);
	let number = $state('');
	let busy = $state(false);
	let done = $state(false);
	let error = $state('');
	let reasonError = $state('');
	let numberError = $state('');
	let key = crypto.randomUUID();

	const inv = $derived(due.invoice);
	const hasCredit = $derived(Number(inv?.total ?? 0) > 0);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (busy || done) return;
		busy = true;
		error = reasonError = numberError = '';
		const body: Record<string, string> = { reason: reason.trim() };
		if (custom) body.number = number.trim();
		const r = await platform<VoidReissueResult>('POST', `/billing/dues/${due.id}/invoice/void-and-reissue`, body, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (r.ok && r.data) {
			done = true;
			ondone(r.data);
			open = false;
			return;
		}
		// A network failure keeps the key so a retry stays idempotent; any answer from the server gets a fresh one.
		if (r.status !== 0) key = crypto.randomUUID();
		if (r.fields.reason) reasonError = r.fields.reason;
		if (r.fields.number) numberError = r.fields.number;
		if (!r.fields.reason && !r.fields.number) error = r.message ?? 'The invoice could not be voided.';
	}
</script>

<Modal bind:open title="Void invoice {inv?.number ?? ''} and issue a replacement" wide {onclose}>
	<form class="flex flex-col gap-4" onsubmit={submit}>
		<ul class="list-disc space-y-1 pl-5 text-sm">
			<li>Invoice <strong class="font-mono">{inv?.number}</strong> is marked <strong>VOID</strong> and keeps its number. Numbers are never reused or edited.</li>
			<li>
				{#if hasCredit}A credit note for the full amount (<strong class="font-mono tabular-nums">{money2(inv?.total)}</strong>) is issued as a document.
				{:else}No credit note is issued: the amount is {money2(inv?.total)}.{/if}
			</li>
			<li>A replacement invoice is issued for the same period, with the next automatic number or your own custom number.</li>
			<li>Nothing changes in the amounts, payments or the balance of {due.club.name}, and no club credit is created.</li>
			<li>{hasCredit ? 'The replacement must be sent to the club again.' : 'The replacement has nothing to send (no amount due).'}</li>
		</ul>

		<div class="flex flex-col gap-1">
			<label class="flex cursor-pointer items-center gap-2 text-sm">
				<input type="checkbox" class="checkbox" bind:checked={custom} /> Use a custom number
			</label>
			{#if custom}
				<Field label="Custom invoice number" required maxlength={32} bind:value={number} error={numberError} help="Only for numbers that must follow an external system. Cannot look like the automatic format set on the Invoicing tab and cannot be changed afterwards." />
			{/if}
		</div>

		<div class="form-control w-full">
			<label class="label" for="void-reason"><span class="label-text">Reason</span></label>
			<textarea
				id="void-reason"
				class="textarea textarea-bordered w-full {reasonError ? 'textarea-error' : ''}"
				rows="3"
				required
				maxlength={MAX_REASON}
				placeholder="Wrong bill-to address, Wrong VAT number"
				aria-invalid={reasonError ? 'true' : undefined}
				bind:value={reason}
			></textarea>
			<div class="mt-1 flex justify-between gap-2 text-xs">
				<span class="text-club-dark">{reasonError}</span>
				<span class="font-mono text-muted">{reason.length}/{MAX_REASON}</span>
			</div>
		</div>

		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>Cancel</button>
			<button class="btn btn-error" disabled={busy || done || !reason.trim() || (custom && !number.trim())}>{busy ? 'Working…' : 'Void and reissue'}</button>
		</div>
	</form>
</Modal>
