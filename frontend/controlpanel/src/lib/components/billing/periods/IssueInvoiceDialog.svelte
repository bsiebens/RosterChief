<script lang="ts">
	import { page } from '$app/state';
	import { platform } from '#lib/api.ts';
	import type { Due } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { day, money2 } from './format.ts';

	let { due, ondone, onclose }: { due: Due; ondone: () => void; onclose: () => void } = $props();

	// Automatic invoicing off (known on the billing pages): numbers are typed by hand, so the field is always shown and required.
	const manual = $derived((page.data.summary as { auto_invoicing?: boolean } | null | undefined)?.auto_invoicing === false);
	let open = $state(true);
	let customChoice = $state(false);
	const custom = $derived(manual || customChoice);
	let number = $state('');
	let busy = $state(false);
	let error = $state('');
	let fieldError = $state('');
	let key = crypto.randomUUID();

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = fieldError = '';
		const r = await platform('POST', `/billing/dues/${due.id}/invoice`, custom ? { number: number.trim() } : {}, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (r.ok) {
			ondone();
			open = false;
			return;
		}
		if (r.status !== 0) key = crypto.randomUUID();
		if (r.fields.number) fieldError = r.fields.number;
		else error = r.message ?? 'The invoice could not be issued.';
	}
</script>

<Modal bind:open title="Issue invoice" {onclose}>
	<form class="flex flex-col gap-3" onsubmit={submit}>
		<p class="text-sm">
			Issue the invoice for <strong>{due.club.name}</strong>, {day(due.period_start)} – {day(due.period_end)} ({money2(due.total)} incl. VAT). By default it gets the next number of the automatic sequence (format set on the Invoicing tab).
		</p>
		<label class="flex cursor-pointer items-center gap-2 text-sm">
			{#if manual}<span class="text-xs text-muted">Automatic invoicing is off: type the invoice number.</span>{:else}<input type="checkbox" class="checkbox" bind:checked={customChoice} /> Use a custom number (for an invoice issued outside the system){/if}
		</label>
		{#if custom}
			<Field label="Custom invoice number" required maxlength={32} bind:value={number} error={fieldError} help="1–32 characters, unique. It must not look like the automatic format set on the Invoicing tab." />
		{/if}
		<Alert kind="warning" title="Numbers are permanent">
			An invoice number can never be changed once created. Custom numbers never affect the automatic sequence, which stays gapless.
		</Alert>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>Cancel</button>
			<button class="btn btn-primary" disabled={busy || (custom && !number.trim())}>{busy ? 'Issuing…' : 'Issue invoice'}</button>
		</div>
	</form>
</Modal>
