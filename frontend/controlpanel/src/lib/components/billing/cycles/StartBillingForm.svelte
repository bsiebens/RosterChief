<script lang="ts">
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import InvoiceNumberField from '#lib/components/billing/InvoiceNumberField.svelte';
	import { day, newKey, price } from './fmt.ts';
	import type { PlanLite, Unbilled } from './types.ts';

	let { club, plans, archiveAfterDays, nextNumber = null, onclose, ondone }: { club: Unbilled; plans: PlanLite[]; archiveAfterDays: number; nextNumber?: string | null; onclose: () => void; ondone: (message: string) => void } = $props();

	const active = $derived(plans.filter((p) => p.is_active));
	const id = $props.id();
	const key = newKey();

	let planId = $state('');
	let start = $state('');
	let trial = $state(false);
	let trialDays = $state('30');
	let autoRenew = $state(true);
	let autoArchive = $state(true);
	let notes = $state('');
	let invoiceNumber = $state<string | null>(null);
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});

	const label = (p: PlanLite) => `${p.plan.name} — ${p.current_price == null ? 'no price yet' : `€ ${price(p.current_price)} net`}${p.terms ? ` / ${p.terms.duration_months} mo` : ''}`;
	const bad = (p: PlanLite) => p.current_price == null;
	const chosen = $derived(active.find((p) => p.plan.id === planId));

	const whole = (v: string) => /^\d+$/.test(v.trim());
	const iso = (d: Date) => d.toISOString().slice(0, 10);
	// Trial runs from the start day for N days; shown as plain days like every other date in billing.
	const startDay = $derived(start || iso(new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()))));
	const trialEnd = $derived.by(() => {
		if (!whole(trialDays) || Number(trialDays) < 1 || Number(trialDays) > 365) return null;
		const d = new Date(`${startDay}T00:00:00Z`);
		if (Number.isNaN(d.getTime())) return null;
		d.setUTCDate(d.getUTCDate() + Number(trialDays));
		return iso(d);
	});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		fields = {};
		if (!planId) return (fields = { plan_id: 'Choose a plan.' });
		if (trial && (!whole(trialDays) || Number(trialDays) < 1 || Number(trialDays) > 365)) return (fields = { trial_days: 'Enter a whole number of days between 1 and 365.' });
		busy = true;
		const r = await platform('POST', `/clubs/${club.id}/billing/subscribe`, { plan_id: planId, start: start || null, trial_days: trial ? Number(trialDays) : null, auto_renew: autoRenew, auto_archive: autoArchive, notes, invoice_number: invoiceNumber }, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			if (r.status === 409 && invoiceNumber && !fields.invoice_number) return (fields = { ...fields, invoice_number: r.message ?? 'That invoice number is already in use.' });
			return (error = r.message ?? 'Billing could not be started.');
		}
		onclose();
		ondone(trial ? `${club.name} is on a free trial. The first paid period is invoiced automatically before it ends.` : `${club.name} is now billed. The first period is open and invoiced.`);
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	<p class="text-sm">Bill <strong>{club.name}</strong> on a plan. Optionally start with a free trial.</p>

	<div class="form-control">
		<label class="label" for="{id}-plan"><span class="label-text">Plan</span></label>
		<select id="{id}-plan" class="select select-bordered w-full {fields.plan_id ? 'select-error' : ''}" bind:value={planId} aria-invalid={fields.plan_id ? 'true' : undefined}>
			<option value="">Choose a plan…</option>
			{#each active as p (p.plan.id)}<option value={p.plan.id} disabled={bad(p)}>{label(p)}</option>{/each}
		</select>
		{#if fields.plan_id}<span class="mt-1 block text-xs text-club-dark">{fields.plan_id}</span>{/if}
	</div>

	<div class="form-control">
		<label class="label" for="{id}-start"><span class="label-text">Start date (optional)</span></label>
		<input id="{id}-start" class="input input-bordered w-full" type="date" bind:value={start} aria-describedby="{id}-start-help" aria-invalid={fields.start ? 'true' : undefined} />
		<span id="{id}-start-help" class="label-text-alt mt-1 block {fields.start ? 'text-club-dark' : ''}">{fields.start ?? 'Leave empty to start today. This date becomes the club’s anniversary: every later period starts on it.'}</span>
	</div>

	<div class="flex flex-col gap-3 text-sm">
		<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" class="checkbox" bind:checked={trial} /> Start with a free trial</label>
		{#if trial}
			<div class="form-control">
				<label class="label" for="{id}-trial"><span class="label-text">Trial length (days)</span></label>
				<input id="{id}-trial" class="input input-bordered w-32 {fields.trial_days ? 'input-error' : ''}" type="number" min="1" max="365" step="1" inputmode="numeric" bind:value={trialDays} aria-invalid={fields.trial_days ? 'true' : undefined} aria-describedby="{id}-trial-d" />
				<span id="{id}-trial-d" class="mt-1 block text-xs {fields.trial_days ? 'text-club-dark' : 'label-text-alt'}">
					{#if fields.trial_days}{fields.trial_days}
					{:else if trialEnd}
						Free from {day(startDay)} to {day(trialEnd)}.
						{#if chosen}The first paid period ({chosen.current_price == null ? 'no price yet' : `€ ${price(chosen.current_price)}`} + VAT) is then invoiced automatically {chosen.terms?.renewal_lead_days ?? 'a few'} days before the trial ends and the club has {chosen.terms?.grace_days ?? 'a number of'} grace days to pay.
						{:else}Choose a plan to see what is billed after the trial.{/if}
					{:else}Enter a whole number of days between 1 and 365.{/if}
				</span>
			</div>
		{/if}
		<div>
			<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" class="checkbox" bind:checked={autoRenew} /> Renew automatically</label>
			<span class="label-text-alt mt-1 block pl-8">The next period is opened and invoiced ahead of time.</span>
		</div>
		<div>
			<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" class="checkbox" bind:checked={autoArchive} /> Archive when overdue</label>
			<span class="label-text-alt mt-1 block pl-8">When a period stays unpaid past its grace the club is first moved to the free plan, and archived {archiveAfterDays} days later.</span>
		</div>
	</div>

	<InvoiceNumberField bind:value={invoiceNumber} nextAutomatic={nextNumber} error={fields.invoice_number} extra={trial ? 'For a trial the number goes on the trial’s zero invoice.' : ''} />

	<div class="form-control">
		<label class="label" for="{id}-notes"><span class="label-text">Notes</span></label>
		<textarea id="{id}-notes" class="textarea textarea-bordered w-full" rows="2" bind:value={notes}></textarea>
	</div>

	<div class="modal-action mt-0">
		<button type="button" class="btn btn-ghost btn-sm" onclick={onclose} disabled={busy}>Cancel</button>
		<button type="submit" class="btn btn-primary btn-sm" disabled={busy}>{busy ? 'Saving…' : 'Start billing'}</button>
	</div>
</form>
