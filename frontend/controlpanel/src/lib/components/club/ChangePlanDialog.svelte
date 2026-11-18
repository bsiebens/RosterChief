<script lang="ts">
	// Plan change for a BILLED club: prorates the running period, issues credit note(s) and starts a new cycle on the
	// new plan. Every change of plan or date re-runs a read-only preview; nothing is written until "Change plan…".
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { BillingPlan, PlanChangePreview, PlanChangeResult } from '#lib/billing.ts';
	import type { ClubBilling, ClubDetail, Plan } from '#lib/clubs.ts';
	import { day, money2, todayLocal, vatPct } from '#lib/components/billing/periods/format.ts';
	import InvoiceNumberField from '#lib/components/billing/InvoiceNumberField.svelte';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let {
		open = $bindable(false),
		club,
		billing,
		plans,
		currentPlanId,
		isSuperuser,
		ondone
	}: { open: boolean; club: ClubDetail; billing: ClubBilling | null; plans: Plan[]; currentPlanId: string | null; isSuperuser: boolean; ondone: (r: PlanChangeResult) => void } = $props();

	const today = todayLocal();
	const addDay = (iso: string, n: number) => {
		const [y, m, d] = iso.split('-').map(Number);
		return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
	};

	// The platform's billing plans (with their cycles), loaded when the dialog opens: the options are every active plan and billing cycle but the club's own.
	let billingPlans = $state<BillingPlan[]>([]);
	$effect(() => {
		if (!open) return;
		void platform<BillingPlan[]>('GET', '/billing/plans').then((r) => (billingPlans = r.data ?? []));
	});
	const currentPlan = $derived(currentPlanId ?? billing?.plan?.id ?? null);
	const currentCycleId = $derived(billing?.cycle?.id ?? null);
	type Choice = { key: string; label: string; disabled: boolean };
	const choices = $derived.by<Choice[]>(() => {
		const out: Choice[] = [];
		for (const plan of plans.filter((p) => p.is_active)) {
			const cycles = (billingPlans.find((b) => b.plan.id === plan.id)?.cycles ?? []).filter((c) => c.is_active);
			if (!cycles.length) {
				if (plan.id !== currentPlan) out.push({ key: `${plan.id}:`, label: plan.name, disabled: false });
				continue;
			}
			for (const c of cycles) {
				if (plan.id === currentPlan && c.id === currentCycleId) continue;
				const price = c.current_price == null ? 'no price yet' : `€ ${Number(c.current_price).toFixed(2)} net`;
				out.push({ key: `${plan.id}:${c.id}`, label: cycles.length > 1 || plan.id === currentPlan ? `${plan.name} · ${c.name} (${price})` : plan.name, disabled: c.current_price == null });
			}
		}
		return out;
	});
	const planOf = (key: string) => key.split(':')[0];
	const cycleOf = (key: string) => key.split(':')[1] || null;
	// The period running today (an advance-invoiced next period does not count): the switch date must be after its start.
	const running = $derived(billing?.dues.find((d) => d.period_start <= today && d.period_end >= today && d.status !== 'cancelled') ?? null);
	const minDate = $derived(running ? addDay(running.period_start, 1) : undefined);

	let planId = $state('');
	let effective = $state(today);
	let preview = $state<PlanChangePreview | null>(null);
	let loading = $state(false);
	let previewError = $state('');
	let previewFields = $state<Record<string, string>>({});
	let invoiceNumber = $state<string | null>(null);
	let numberError = $state('');
	let submitError = $state('');
	let busy = $state(false);
	let key = '';
	let seq = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;

	// Reset whenever the dialog opens.
	$effect(() => {
		if (!open) return;
		untrack(() => {
			planId = choices.find((c) => !c.disabled)?.key ?? '';
			effective = today;
			preview = null;
			previewError = submitError = numberError = '';
			invoiceNumber = null;
			previewFields = {};
			key = crypto.randomUUID();
		});
	});

	// Debounced preview on every plan / date change. Only the latest answer is kept.
	$effect(() => {
		const id = planId;
		const date = effective;
		const num = invoiceNumber;
		if (!open || !isSuperuser || !id || !date) return;
		clearTimeout(timer);
		const mine = ++seq;
		untrack(() => {
			loading = true;
			previewError = '';
			previewFields = {};
		});
		timer = setTimeout(async () => {
			const r = await platform<PlanChangePreview>('POST', `/clubs/${club.id}/billing/change-plan/preview`, { plan_id: planOf(id), cycle_id: cycleOf(id), effective_date: date, invoice_number: num });
			if (mine !== seq) return;
			loading = false;
			if (r.ok && r.data) preview = r.data;
			else {
				if (!num || !(r.fields.invoice_number || r.status === 409)) preview = null;
				previewError = r.message ?? 'The preview could not be calculated.';
				previewFields = r.fields;
			}
		}, 350);
		return () => clearTimeout(timer);
	});

	// A changed plan or date invalidates a pending idempotency key (different request body).
	$effect(() => {
		void planId;
		void effective;
		void invoiceNumber;
		untrack(() => {
			key = crypto.randomUUID();
			numberError = '';
		});
	});

	async function confirm() {
		if (!preview) return;
		busy = true;
		submitError = '';
		const r = await platform<PlanChangeResult>('POST', `/clubs/${club.id}/billing/change-plan`, { plan_id: planOf(planId), cycle_id: cycleOf(planId), effective_date: effective, invoice_number: invoiceNumber }, fetch, { 'Idempotency-Key': key });
		busy = false;
		if (!r.ok || !r.data) {
			if (r.status !== 0) key = crypto.randomUUID();
			if (r.fields.invoice_number || (r.status === 409 && invoiceNumber)) return (numberError = r.fields.invoice_number ?? r.message ?? '');
			submitError = r.message ?? 'The plan could not be changed.';
			return;
		}
		open = false;
		ondone(r.data);
	}

	const pct = (used: number, total: number) => (total ? Math.min(100, Math.round((used / total) * 100)) : 0);
</script>

<Modal bind:open title="Change plan and start a new cycle" wide>
	<div class="flex flex-col gap-4">
		<p class="text-sm text-muted">
			{club.name} is billed. Switching plan or billing cycle shortens and prorates the running period, issues a credit note for the unused part, and starts a new full period on the new plan and cycle from the switch date.
		</p>

		{#if !isSuperuser}
			<Alert kind="info">Only superusers can change a billed club's plan. Ask a superuser to make this change.</Alert>
		{/if}

		<div class="grid gap-3 sm:grid-cols-2">
			<div class="form-control w-full">
				<label class="label" for="cp-plan"><span class="label-text">New plan and billing cycle</span></label>
				<select id="cp-plan" class="select select-bordered w-full" bind:value={planId} disabled={!isSuperuser}>
					{#each choices as choice (choice.key)}<option value={choice.key} disabled={choice.disabled}>{choice.label}</option>{/each}
				</select>
			</div>
			<Field label="Switch date" type="date" max={today} min={minDate} bind:value={effective} disabled={!isSuperuser} error={previewFields.effective_date} help="Defaults to today. You can back-date within the current period; features change immediately either way." />
		</div>

		{#if previewError && !previewFields.effective_date && !previewFields.invoice_number}<Alert kind="error">{previewError}</Alert>{/if}

		{#if isSuperuser}
			<InvoiceNumberField bind:value={invoiceNumber} nextAutomatic={preview?.invoice_number?.next_automatic ?? null} error={numberError || previewFields.invoice_number || (previewError && invoiceNumber && !previewFields.effective_date ? previewError : '')} />
		{/if}

		{#if isSuperuser && preview}
			{@const p = preview}
			{@const o = p.old_period}
			<div class="flex flex-col gap-3 {loading ? 'opacity-60' : ''}" aria-busy={loading}>
				{#each p.warnings as w (w)}<Alert kind="warning">{w}</Alert>{/each}

				<section class="rounded border border-rule bg-paper p-3">
					<h3 class="mb-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">Current period{o ? ` on ${p.from_plan?.name ?? 'the current plan'}` : ''}</h3>
					{#if o}
						<div class="font-mono text-xs">
							{day(o.period_start)} – {day(o.period_end)} <span class="text-muted">→ ends {day(o.new_period_end)}</span>
							{#if o.is_trial}<span class="badge badge-info badge-xs ml-1">trial</span>{/if}
						</div>
						<div class="mt-2" role="img" aria-label="{o.used_days} of {o.total_days} days used">
							<div class="h-2 overflow-hidden rounded bg-edge"><div class="h-full bg-ice" style="width: {pct(o.used_days, o.total_days)}%"></div></div>
							<div class="mt-1 flex justify-between font-mono text-[11px] text-muted"><span>{o.used_days} days used</span><span>{o.unused_days} unused of {o.total_days}</span></div>
						</div>
						<table class="table mt-2 text-sm">
							<thead><tr><th></th><th class="text-right">Net</th><th class="text-right">VAT</th><th class="text-right">Total</th></tr></thead>
							<tbody class="font-mono tabular-nums">
								<tr><td class="font-sans text-muted">Original{o.invoice_number ? ` (${o.invoice_number})` : ''}</td><td class="text-right">{money2(o.original.net)}</td><td class="text-right">{money2(o.original.vat)}</td><td class="text-right">{money2(o.original.total)}</td></tr>
								<tr class="font-semibold"><td class="font-sans">Prorated</td><td class="text-right">{money2(o.prorated.net)}</td><td class="text-right">{money2(o.prorated.vat)}</td><td class="text-right">{money2(o.prorated.total)}</td></tr>
							</tbody>
						</table>
						<ul class="mt-2 list-disc space-y-0.5 pl-5 text-sm">
							{#if o.credit_to_issue}
								<li>A <strong>credit note</strong> of <strong>{money2(o.credit_to_issue.total)}</strong> ({money2(o.credit_to_issue.net)} + {money2(o.credit_to_issue.vat)} VAT) is issued for the unused part{o.invoice_number ? ` of ${o.invoice_number}` : ''}. Its number is assigned when you confirm. The original invoice is not edited.</li>
							{:else}
								<li>No credit note is needed for this period{o.has_invoice ? '' : ' (it has no invoice)'}.</li>
							{/if}
							{#if Number(o.paid)}<li>Paid so far: {money2(o.paid)}.</li>{/if}
							{#if Number(o.overpayment_to_credit)}<li><strong>{money2(o.overpayment_to_credit)}</strong> paid beyond the prorated amount moves to the club's credit.</li>{/if}
						</ul>
					{:else}
						<p class="text-sm text-muted">No billing period covers {day(p.effective_date)}, so nothing is prorated.</p>
					{/if}
				</section>

				{#if p.cancelled_periods.length}
					<section class="rounded border border-rule bg-paper p-3">
						<h3 class="mb-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">Cancelled in advance-invoiced periods</h3>
						<ul class="space-y-1 text-sm">
							{#each p.cancelled_periods as c (c.id)}
								<li>
									<span class="font-mono text-xs">{day(c.period_start)} – {day(c.period_end)}</span>{c.invoice_number ? ` · ${c.invoice_number}` : ''}: {money2(c.total)} credited in full{#if Number(c.paid)}, {money2(c.paid)} already paid becomes club credit{/if}.
								</li>
							{/each}
						</ul>
						{#if p.credit_notes_to_issue.length > 1}
							<p class="mt-1 text-xs text-muted">{p.credit_notes_to_issue.length} credit notes in total will be issued.</p>
						{/if}
					</section>
				{/if}

				<section class="rounded border border-rule bg-paper p-3">
					<h3 class="mb-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">New cycle on {p.new_period.plan.name}{p.new_period.cycle ? ` · ${p.new_period.cycle.name}` : ''}</h3>
					<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
						<dt class="text-muted">Period</dt><dd class="font-mono text-xs">{day(p.new_period.period_start)} – {day(p.new_period.period_end)}</dd>
						<dt class="text-muted">Net</dt><dd class="font-mono tabular-nums">{money2(p.new_period.net)}</dd>
						<dt class="text-muted">VAT</dt><dd class="font-mono tabular-nums">{money2(p.new_period.vat_amount)} {p.new_period.reverse_charge ? '(reverse charge)' : `(${vatPct(p.new_period.vat_rate)})`}</dd>
						<dt class="text-muted">Total</dt><dd class="font-mono font-semibold tabular-nums">{money2(p.new_period.total)}</dd>
						<dt class="text-muted">Grace until</dt><dd>{day(p.new_period.grace_until)}</dd>
						<dt class="text-muted">Invoice</dt><dd>issued immediately</dd>
						{#if p.invoice_number}<dt class="text-muted">Invoice number</dt><dd class="font-mono text-xs">{#if p.invoice_number.mode === 'custom'}custom {p.invoice_number.number}{:else}automatic{p.invoice_number.next_automatic ? ` (next: ${p.invoice_number.next_automatic})` : ''}{/if}</dd>{/if}
					</dl>
				</section>

				<section class="rounded border-2 border-ink bg-white p-3" aria-live="polite">
					<dl class="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
						<dt class="text-muted">Club credit available</dt><dd class="text-right font-mono tabular-nums">{money2(p.credit_available_for_new_period)}</dd>
						<dt class="text-muted">Credit applied to the new invoice</dt><dd class="text-right font-mono tabular-nums">{money2(p.credit_applied)}</dd>
						<dt class="font-bold text-ink">Amount payable now</dt><dd class="text-right font-mono text-lg font-extrabold tabular-nums text-ink">{money2(p.amount_payable_after_credit)}</dd>
						<dt class="text-muted">Club credit afterwards</dt><dd class="text-right font-mono font-semibold tabular-nums {Number(p.credit_balance_after) > 0 ? 'text-ok-text' : ''}">{money2(p.credit_balance_after)}</dd>
					</dl>
					<p class="mt-2 text-xs text-muted">Credit is never refunded by the system; it is applied to later invoices.</p>
				</section>
			</div>
		{:else if isSuperuser && loading}
			<p class="text-sm text-muted">Calculating the preview…</p>
		{/if}

		{#if submitError}<Alert kind="error">{submitError}</Alert>{/if}

		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>{isSuperuser ? 'Cancel' : 'Close'}</button>
			{#if isSuperuser}
				<button type="button" class="btn btn-primary" onclick={confirm} disabled={busy || loading || !preview || !!previewError}>{busy ? 'Changing…' : 'Change plan and start a new cycle'}</button>
			{/if}
		</div>
	</div>
</Modal>
