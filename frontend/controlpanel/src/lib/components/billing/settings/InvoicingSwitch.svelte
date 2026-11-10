<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';

	/** The platform switch for automatic invoicing. Off: nothing is opened or invoiced by the daily job and platform admins are warned. */
	let { enabled, canEdit }: { enabled: boolean; canEdit: boolean } = $props();

	// svelte-ignore state_referenced_locally
	let on = $state(enabled);
	let confirming = $state(false);
	let busy = $state(false);
	let error = $state('');
	$effect(() => {
		on = enabled;
	});

	async function save(next: boolean) {
		busy = true;
		error = '';
		const r = await platform('PUT', '/billing/settings/auto-invoicing', { enabled: next });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The switch could not be changed.');
		on = next;
		confirming = false;
		await invalidate('app:billing');
	}

	function toggle() {
		if (on) confirming = true;
		else save(true);
	}
</script>

<section class="card card-body gap-3" aria-labelledby="auto-invoicing-h">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div>
			<h2 id="auto-invoicing-h" class="font-display text-lg leading-none font-extrabold tracking-[.06em] text-ink uppercase">Automatic invoicing</h2>
			<p class="mt-1.5 font-mono text-[11px] text-muted">{on ? 'On: periods are opened and invoiced by the daily job' : 'Off: periods and invoices are made by hand'}</p>
		</div>
		<label class="flex cursor-pointer items-center gap-3 text-sm font-semibold">
			<input type="checkbox" class="toggle" checked={on} disabled={!canEdit || busy} onchange={toggle} aria-label="Automatic invoicing" />
			{on ? 'On' : 'Off'}
		</label>
	</div>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	<ul class="list-disc space-y-1 pl-5 text-sm text-muted">
		<li><strong class="text-ink">On (default):</strong> every day the next period of each club is opened and invoiced when its plan's “invoice this many days before the period starts” has been reached, with the next automatic invoice number.</li>
		<li>
			<strong class="text-ink">Off:</strong> nothing is opened or invoiced automatically and no automatic number is used. Every platform admin gets a weekly overview of the clubs that need an invoice (already inside their invoicing window, or entering it within a week) and one alert the day a club enters its window. Open the period and create the invoice with the number you type on <a class="link font-semibold" href="/billing/cycles">Cycles</a>, for example when invoice numbers are shared with another platform.
		</li>
		{#if !canEdit}<li>Only a superuser can change this.</li>{/if}
	</ul>
</section>

<Modal bind:open={confirming} title="Turn automatic invoicing off?">
	<div class="flex flex-col gap-3">
		<p class="text-sm">From now on no period is opened and no invoice is created by the daily job. Clubs whose period is about to end will not be invoiced until someone does it by hand, so their next period will not exist and they may lapse.</p>
		<p class="text-sm text-muted">Platform admins are warned by email (weekly overview and a first alert per club and period). Invoices you create by hand need a number you type; the automatic sequence is not used.</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="modal-action mt-0">
			<button class="btn btn-ghost btn-sm" onclick={() => (confirming = false)} disabled={busy}>Keep it on</button>
			<button class="btn btn-primary btn-sm" onclick={() => save(false)} disabled={busy}>{busy ? 'Saving…' : 'Turn it off'}</button>
		</div>
	</div>
</Modal>
