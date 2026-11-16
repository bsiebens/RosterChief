<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { BillingPlan } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';

	let { plan, onclose, ondone }: { plan: BillingPlan; onclose: () => void; ondone: (message: string) => void } = $props();

	const id = $props.id();
	const t = untrack(() => plan.terms) ?? { duration_months: 12, renewal_lead_days: 30, grace_days: 30 };
	let months = $state(String(t.duration_months));
	let lead = $state(String(t.renewal_lead_days));
	let grace = $state(String(t.grace_days));
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});

	const whole = (v: string) => /^\d+$/.test(v.trim());

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		fields = {};
		if (!whole(months) || Number(months) < 1) fields.duration_months = 'Enter a whole number of months, at least 1.';
		if (!whole(lead)) fields.renewal_lead_days = 'Enter a whole number of days (0 or more).';
		if (!whole(grace)) fields.grace_days = 'Enter a whole number of days (0 or more).';
		if (Object.keys(fields).length) return;
		busy = true;
		const r = await platform('PUT', `/billing/plans/${plan.plan.id}/terms`, { duration_months: Number(months), renewal_lead_days: Number(lead), grace_days: Number(grace) });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			return (error = r.message ?? 'The terms could not be saved.');
		}
		onclose();
		ondone(`Terms of ${plan.plan.name} saved. They apply to periods opened from now on.`);
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	<p class="text-xs text-muted">Periods that are already open keep the dates they were created with.</p>

	{#each [{ k: 'duration_months', label: 'Period length (months)', help: 'How long one billing period lasts. A period starts on the club’s anniversary.', get: () => months, set: (v: string) => (months = v) }, { k: 'renewal_lead_days', label: 'Invoice lead time (days)', help: 'Invoice this many days before the period starts.', get: () => lead, set: (v: string) => (lead = v) }, { k: 'grace_days', label: 'Grace period (days)', help: 'Counted from the period START, not its end: a club is overdue this many days after the period begins.', get: () => grace, set: (v: string) => (grace = v) }] as f (f.k)}
		<div class="form-control">
			<label class="label" for="{id}-{f.k}"><span class="label-text">{f.label}</span></label>
			<input
				id="{id}-{f.k}"
				class="input input-bordered w-full {fields[f.k] ? 'input-error' : ''}"
				type="number"
				min={f.k === 'duration_months' ? 1 : 0}
				step="1"
				inputmode="numeric"
				value={f.get()}
				oninput={(e) => f.set(e.currentTarget.value)}
				aria-invalid={fields[f.k] ? 'true' : undefined}
				aria-describedby="{id}-{f.k}-d"
			/>
			<span id="{id}-{f.k}-d" class="mt-1 block text-xs {fields[f.k] ? 'text-club-dark' : 'label-text-alt'}">{fields[f.k] ?? f.help}</span>
		</div>
	{/each}

	<div class="modal-action mt-0">
		<button type="button" class="btn btn-ghost btn-sm" onclick={onclose} disabled={busy}>Cancel</button>
		<button type="submit" class="btn btn-primary btn-sm" disabled={busy}>{busy ? 'Saving…' : 'Save terms'}</button>
	</div>
</form>
