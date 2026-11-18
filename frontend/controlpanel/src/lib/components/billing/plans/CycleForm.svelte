<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { BillingPlan, PlanCycle } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';

	/** Add a billing cycle to a plan (monthly, quarterly, yearly ...) or change one. Changes apply to periods opened from now on. */
	let { plan, cycle = null, onclose, ondone }: { plan: BillingPlan; cycle?: PlanCycle | null; onclose: () => void; ondone: (message: string) => void } = $props();

	const id = $props.id();
	const start = untrack(() => cycle);
	let label = $state(start?.label ?? '');
	let months = $state(String(start?.duration_months ?? 1));
	let lead = $state(String(start?.renewal_lead_days ?? 7));
	let grace = $state(String(start?.grace_days ?? 14));
	let active = $state(start?.is_active ?? true);
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});

	const whole = (v: string) => /^\d+$/.test(v.trim());
	const QUICK = [
		{ months: 1, label: 'Monthly' },
		{ months: 3, label: 'Quarterly' },
		{ months: 6, label: 'Half-yearly' },
		{ months: 12, label: 'Yearly' }
	];
	const taken = $derived(plan.cycles.some((c) => c.duration_months === Number(months) && c.id !== cycle?.id));

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		fields = {};
		if (!whole(months) || Number(months) < 1 || Number(months) > 120) fields.duration_months = 'Enter a whole number of months, from 1 to 120.';
		else if (taken) fields.duration_months = 'This plan already has a billing cycle of that length.';
		if (!whole(lead)) fields.renewal_lead_days = 'Enter a whole number of days (0 or more).';
		if (!whole(grace)) fields.grace_days = 'Enter a whole number of days (0 or more).';
		if (Object.keys(fields).length) return;
		busy = true;
		const body = { label: label.trim(), duration_months: Number(months), renewal_lead_days: Number(lead), grace_days: Number(grace), is_active: active };
		const r = cycle ? await platform('PATCH', `/billing/cycles/${cycle.id}`, body) : await platform('POST', `/billing/plans/${plan.plan.id}/cycles`, body);
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			return (error = r.message ?? 'The billing cycle could not be saved.');
		}
		onclose();
		ondone(cycle ? `Billing cycle of ${plan.plan.name} saved. It applies to periods opened from now on.` : `Billing cycle added to ${plan.plan.name}. Add a price before clubs can be billed on it.`);
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	<p class="text-xs text-muted">Periods that are already open keep the dates they were created with.</p>

	{#if !cycle}
		<div class="flex flex-wrap gap-1.5" role="group" aria-label="Common lengths">
			{#each QUICK as q (q.months)}
				<button type="button" class="btn btn-xs {Number(months) === q.months ? '' : 'btn-outline'}" disabled={plan.cycles.some((c) => c.duration_months === q.months)} onclick={() => ((months = String(q.months)), (lead = String(q.months === 1 ? 7 : q.months === 3 ? 14 : 30)), (grace = String(q.months === 1 ? 14 : 30)))}>{q.label}</button>
			{/each}
		</div>
	{/if}

	<div class="form-control">
		<label class="label" for="{id}-label"><span class="label-text">Name (optional)</span></label>
		<input id="{id}-label" class="input input-bordered w-full" bind:value={label} maxlength="60" placeholder="Empty = named after its length, such as Monthly or Yearly" />
	</div>

	{#each [{ k: 'duration_months', label: 'Period length (months)', help: 'How long one billing period lasts. A period starts on the club’s anniversary.', get: () => months, set: (v: string) => (months = v) }, { k: 'renewal_lead_days', label: 'Invoice this many days before the period starts', help: 'When the next period is opened and invoiced ahead of time (must be shorter than the period).', get: () => lead, set: (v: string) => (lead = v) }, { k: 'grace_days', label: 'Grace days', help: 'How long after the period start (or the day it was opened, whichever is later) the club can pay before it is downgraded.', get: () => grace, set: (v: string) => (grace = v) }] as f (f.k)}
		<div class="form-control">
			<label class="label" for="{id}-{f.k}"><span class="label-text">{f.label}</span></label>
			<input id="{id}-{f.k}" class="input input-bordered w-full {fields[f.k] ? 'input-error' : ''}" type="number" min={f.k === 'duration_months' ? 1 : 0} step="1" inputmode="numeric" value={f.get()} oninput={(e) => f.set(e.currentTarget.value)} aria-invalid={fields[f.k] ? 'true' : undefined} aria-describedby="{id}-{f.k}-d" />
			<span id="{id}-{f.k}-d" class="mt-1 block text-xs {fields[f.k] ? 'text-club-dark' : 'label-text-alt'}">{fields[f.k] ?? f.help}</span>
		</div>
	{/each}

	<label class="flex cursor-pointer items-start gap-2 text-sm">
		<input type="checkbox" class="checkbox mt-0.5" bind:checked={active} />
		<span>Active<span class="label-text-alt mt-0.5 block">Inactive cycles cannot be chosen for a club any more. Clubs already on it keep renewing.</span></span>
	</label>

	<div class="modal-action mt-0">
		<button type="button" class="btn btn-ghost btn-sm" onclick={onclose} disabled={busy}>Cancel</button>
		<button type="submit" class="btn btn-primary btn-sm" disabled={busy}>{busy ? 'Saving…' : cycle ? 'Save cycle' : 'Add cycle'}</button>
	</div>
</form>
