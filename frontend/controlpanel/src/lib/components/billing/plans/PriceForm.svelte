<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { BillingPlan } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import { day, newKey, price } from '#lib/components/billing/cycles/fmt.ts';

	let { plan, onclose, ondone }: { plan: BillingPlan; onclose: () => void; ondone: (message: string) => void } = $props();

	const id = $props.id();
	const key = newKey();
	const today = new Date().toLocaleDateString('en-CA');
	let from = $state(today);
	let amount = $state('');
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});

	const taken = $derived(plan.prices.some((p) => p.active_from === from));
	const past = $derived(!!from && from < today);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		fields = {};
		if (!from) fields.active_from = 'Choose the date the price starts to apply.';
		else if (taken) fields.active_from = 'A price already exists for that date. Choose another date.';
		const a = amount.trim().replace(',', '.');
		if (!/^\d+(\.\d{1,2})?$/.test(a)) fields.amount = 'Enter an amount of 0 or more with at most two decimals, for example 50.00.';
		if (Object.keys(fields).length) return;
		busy = true;
		const r = await platform('POST', `/billing/plans/${plan.plan.id}/prices`, { active_from: from, amount: a }, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			return (error = r.message ?? 'The price could not be added.');
		}
		onclose();
		ondone(`Price of € ${price(a)} for ${plan.plan.name} applies from ${day(from)}.`);
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	<Alert kind="info">A rate change is always a new dated price: older prices stay in the history. Periods that are already open keep the amount they were billed at. Prices are net, excluding VAT.</Alert>

	<div class="form-control">
		<label class="label" for="{id}-from"><span class="label-text">Valid from</span></label>
		<input id="{id}-from" class="input input-bordered w-full font-mono {fields.active_from ? 'input-error' : ''}" type="date" bind:value={from} aria-invalid={fields.active_from ? 'true' : undefined} aria-describedby="{id}-from-d" />
		<span id="{id}-from-d" class="mt-1 block text-xs {fields.active_from ? 'text-club-dark' : 'label-text-alt'}">
			{fields.active_from ?? (from > today ? 'A future date: the price is scheduled and applies to periods that start on or after it.' : past ? 'This date is in the past. It only affects periods that have not been opened yet.' : 'Applies to periods that start on or after this date.')}
		</span>
	</div>

	<div class="form-control">
		<label class="label" for="{id}-amount"><span class="label-text">Net amount per period (EUR, excl. VAT)</span></label>
		<input id="{id}-amount" class="input input-bordered w-full font-mono {fields.amount ? 'input-error' : ''}" inputmode="decimal" placeholder="50.00" bind:value={amount} aria-invalid={fields.amount ? 'true' : undefined} aria-describedby={fields.amount ? `${id}-amount-d` : undefined} />
		{#if fields.amount}<span id="{id}-amount-d" class="mt-1 block text-xs text-club-dark">{fields.amount}</span>{/if}
	</div>

	<div class="modal-action mt-0">
		<button type="button" class="btn btn-ghost btn-sm" onclick={onclose} disabled={busy}>Cancel</button>
		<button type="submit" class="btn btn-primary btn-sm" disabled={busy}>{busy ? 'Saving…' : 'Add price'}</button>
	</div>
</form>
