<script lang="ts">
	import { refreshAll } from '$app/navigation';
	import type { Cycle } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import CycleTable from '#lib/components/billing/cycles/CycleTable.svelte';
	import OpenPeriodDialog from '#lib/components/billing/cycles/OpenPeriodDialog.svelte';
	import StartBillingDialog from '#lib/components/billing/cycles/StartBillingDialog.svelte';
	import UnbilledList from '#lib/components/billing/cycles/UnbilledList.svelte';
	import type { Unbilled } from '#lib/components/billing/cycles/types.ts';

	let { data } = $props();

	const canAct = $derived(data.auth.status === 'authenticated');
	let opening = $state<Cycle | null>(null);
	let starting = $state<Unbilled | null>(null);
	let notice = $state('');

	function done(message: string) {
		notice = message;
		refreshAll();
	}
	const dueNow = $derived(data.cycles.filter((c) => c.invoice_due_now && !c.opened).length);
</script>

<svelte:head><title>Billing cycles · RosterChief</title></svelte:head>

<Alert kind="info" title="How cycles work">
	Every club is billed on its own anniversary: a period starts on the day the club subscribed and lasts as long as its plan says (for example 12 months). The next period is invoiced in advance, the plan's lead time (for example 30 days) before it starts, and a scheduled job opens it on that day for clubs that renew automatically. Use “Open next period now” to do it early. Trials are set per club: any plan can be trialled, and after the free days the club stays on the same plan and the first paid period is opened and invoiced automatically. A lapsed club (its last period has ended and nothing new was opened, for example because auto-renew is off) gets the next period opened automatically and then follows the normal overdue flow. After the grace period an unpaid club is downgraded to the free plan, and archived after the configured number of days if its auto-archive is on. Paying or waiving the overdue periods restores it at once. <a class="link font-semibold" href="/billing/help">Read the full explanation</a>.
</Alert>

{#if notice}<Alert kind="success">{notice}</Alert>{/if}

<section class="flex flex-col gap-3" aria-labelledby="renewals-h">
	<div>
		<h2 id="renewals-h" class="font-display text-lg leading-none font-extrabold tracking-[.06em] text-ink uppercase">Upcoming renewals</h2>
		<p class="mt-1.5 font-mono text-[11px] text-muted">
			{data.cycles.length} billed club{data.cycles.length === 1 ? '' : 's'} · <span class={dueNow ? 'text-warn-text' : ''}>{dueNow} invoice{dueNow === 1 ? '' : 's'} due now</span> · soonest first
		</p>
	</div>
	{#if data.cyclesError}
		<Alert kind="error">{data.cyclesError}</Alert>
	{:else if !data.cycles.length}
		<div class="card"><p class="p-6 text-center text-sm text-muted">No clubs are billed yet. Start billing for a club below.</p></div>
	{:else}
		<CycleTable cycles={data.cycles} {canAct} onopen={(c) => (opening = c)} />
	{/if}
</section>

<section class="flex flex-col gap-3" aria-labelledby="unbilled-h">
	<div>
		<h2 id="unbilled-h" class="font-display text-lg leading-none font-extrabold tracking-[.06em] text-ink uppercase">Not billed</h2>
		<p class="mt-1.5 font-mono text-[11px] text-muted">Active clubs without billing use the platform for free until you start billing them.</p>
	</div>
	{#if data.unbilledError}
		<Alert kind="error">{data.unbilledError}</Alert>
	{:else if !data.unbilled.length}
		<div class="card"><p class="p-6 text-center text-sm text-muted">Every active club is billed.</p></div>
	{:else}
		<UnbilledList clubs={data.unbilled} {canAct} onstart={(club) => (starting = club)} />
	{/if}
</section>

<OpenPeriodDialog bind:cycle={opening} nextNumber={data.nextNumber} ondone={done} />
<StartBillingDialog bind:target={starting} plans={data.plans} archiveAfterDays={data.archiveAfterDays} nextNumber={data.nextNumber} ondone={done} />
