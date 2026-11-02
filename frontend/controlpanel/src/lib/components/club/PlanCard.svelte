<script lang="ts">
	import { CreditCard } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { PlanChangeResult } from '#lib/billing.ts';
	import type { ClubBilling, ClubDetail, Plan, Subscription } from '#lib/clubs.ts';
	import ChangePlanDialog from './ChangePlanDialog.svelte';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { club, subscriptions, plans, billing = null, isSuperuser = false, canEdit, onchange }: { club: ClubDetail; subscriptions: Subscription[]; plans: Plan[]; billing?: ClubBilling | null; isSuperuser?: boolean; canEdit: boolean; onchange: () => void } = $props();

	const billed = $derived(!!billing?.billed);
	let billedOpen = $state(false);
	let result = $state<PlanChangeResult | null>(null);

	const current = $derived(subscriptions.find((s) => s.is_current) ?? null);
	const history = $derived(subscriptions.filter((s) => s !== current));
	let showAll = $state(false);

	let changeOpen = $state(false);
	let endOpen = $state(false);
	let planId = $state('');
	let status = $state('active');
	let trialEnds = $state('');
	let reason = $state('');
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let notice = $state('');
	let busy = $state(false);

	function openChange() {
		if (billed) {
			notice = '';
			result = null;
			billedOpen = true;
			return;
		}
		planId = current?.plan.id ?? plans.find((p) => p.is_active && !p.is_default)?.id ?? '';
		status = 'active';
		trialEnds = reason = error = '';
		fields = {};
		changeOpen = true;
	}

	async function change(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		const body: Record<string, unknown> = { plan_id: planId, status, reason: reason.trim() };
		if (status === 'trial' && trialEnds) body.trial_ends_at = new Date(`${trialEnds}T23:59:00`).toISOString();
		const r = await platform('POST', `/clubs/${club.id}/subscriptions`, body, fetch, { 'Idempotency-Key': crypto.randomUUID() });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = r.message ?? '';
			return;
		}
		changeOpen = false;
		notice = 'Plan changed. The new entitlements apply immediately.';
		onchange();
	}

	async function end() {
		busy = true;
		error = '';
		const r = await platform('POST', `/clubs/${club.id}/subscriptions/end`, { reason: reason.trim() });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'Could not end the subscription.');
		endOpen = false;
		notice = 'Subscription ended. The club is on the default plan.';
		onchange();
	}

	const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');
	const tone = (s: string) => (s === 'active' ? 'badge-success' : s === 'trial' ? 'badge-info' : s === 'past_due' ? 'badge-warning' : '');
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title"><CreditCard size={16} aria-hidden="true" /> Plan</div>
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
		{#if result}
			<Alert kind="success" title="Plan changed">
				New cycle on {result.to_plan.name} started{#if result.created.new_invoice_number}, invoice <a class="link font-mono" href="/billing?status=all&q={encodeURIComponent(result.created.new_invoice_number)}">{result.created.new_invoice_number}</a> issued{/if}.
				{#if result.credit_notes.length}Credit note{result.credit_notes.length > 1 ? 's' : ''}: {#each result.credit_notes as cn, i (cn.id)}{i ? ', ' : ''}<a class="link font-mono" href="/billing/credit-notes?q={encodeURIComponent(cn.number)}">{cn.number}</a>{/each}.{/if}
			</Alert>
		{/if}

		{#if current}
			<div class="flex flex-wrap items-center gap-2">
				<span class="font-display text-2xl leading-none font-extrabold text-ink uppercase">{current.plan.name}</span>
				<span class="badge {tone(current.status)}">{current.status.replace('_', ' ')}</span>
			</div>
			<div class="font-mono text-[11px] text-muted">
				since {date(current.starts_at)}{current.trial_ends_at ? ` · trial ends ${date(current.trial_ends_at)}` : ''}{current.ends_at ? ` · ends ${date(current.ends_at)}` : ''}
			</div>
		{:else}
			<p class="text-sm">No subscription: the club uses the <strong>default plan</strong>.</p>
		{/if}

		{#if canEdit}
			<div class="card-actions">
				<button class="btn btn-primary btn-sm" onclick={openChange}>Change plan</button>
				{#if current}<button class="btn btn-outline btn-error btn-sm" onclick={() => ((endOpen = true), (reason = ''), (error = ''))}>End subscription</button>{/if}
			</div>
		{/if}

		{#if history.length}
			<div class="mt-1 border-t border-rule pt-3">
				<div class="mb-1.5 font-mono text-[10px] tracking-[.06em] text-dim uppercase">History</div>
				<ul class="flex flex-col gap-1 font-mono text-[11px]">
					{#each showAll ? history : history.slice(0, 4) as sub (sub.id)}
						<li class="flex gap-2"><span class="w-60 shrink-0 whitespace-nowrap text-dim">{date(sub.starts_at)} → {date(sub.ends_at)}</span><span class="text-ink">{sub.plan.name}</span><span class="text-muted">{sub.status.replace('_', ' ')}</span></li>
					{/each}
				</ul>
				{#if history.length > 4}<button class="btn-link mt-1 text-xs" onclick={() => (showAll = !showAll)}>{showAll ? 'Show less' : `Show all ${history.length}`}</button>{/if}
			</div>
		{/if}
	</div>
</div>

<ChangePlanDialog
	bind:open={billedOpen}
	{club}
	{billing}
	{plans}
	currentPlanId={current?.plan.id ?? billing?.plan?.id ?? null}
	{isSuperuser}
	ondone={(r) => {
		result = r;
		onchange();
	}}
/>

<Modal bind:open={changeOpen} title="Change plan">
	<form class="flex flex-col gap-4" onsubmit={change}>
		<p class="text-sm text-muted">The current subscription ends now and the new plan starts immediately. This doesn't open a billing period; use the billing card for that.</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="form-control">
			<label class="label" for="plan"><span class="label-text">Plan</span></label>
			<select id="plan" class="select select-bordered w-full" required bind:value={planId}>
				{#each plans.filter((p) => p.is_active) as plan (plan.id)}<option value={plan.id}>{plan.name}{plan.is_default ? ' (default)' : ''}</option>{/each}
			</select>
			{#if fields.plan_id}<span class="mt-1 block text-xs text-club-dark">{fields.plan_id}</span>{/if}
		</div>
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="form-control">
				<label class="label" for="sub-status"><span class="label-text">Status</span></label>
				<select id="sub-status" class="select select-bordered w-full" bind:value={status}>
					<option value="active">Active</option>
					<option value="trial">Trial</option>
					<option value="past_due">Past due</option>
				</select>
			</div>
			{#if status === 'trial'}
				<div class="form-control">
					<label class="label" for="trial-ends"><span class="label-text">Trial ends</span></label>
					<input id="trial-ends" class="input input-bordered w-full" type="date" bind:value={trialEnds} />
				</div>
			{/if}
		</div>
		<div class="form-control">
			<label class="label" for="reason"><span class="label-text">Reason <span class="text-muted">(kept in the entitlement log)</span></span></label>
			<input id="reason" class="input input-bordered w-full" maxlength="200" bind:value={reason} />
		</div>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (changeOpen = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !planId}>Change plan</button>
		</div>
	</form>
</Modal>

<Modal bind:open={endOpen} title="End subscription?">
	<p class="text-sm">The club falls back to the default plan right away and may lose features.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="form-control mt-3">
		<label class="label" for="end-reason"><span class="label-text">Reason</span></label>
		<input id="end-reason" class="input input-bordered w-full" maxlength="200" bind:value={reason} />
	</div>
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (endOpen = false)}>Cancel</button>
		<button class="btn btn-error" disabled={busy} onclick={end}>End subscription</button>
	</div>
</Modal>
