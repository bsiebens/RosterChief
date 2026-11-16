<script lang="ts">
	import { goto } from '$app/navigation';
	import { onDestroy } from 'svelte';
	import { ArrowLeft, CircleCheck, LoaderCircle } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { Provisioning } from '#lib/clubs.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	let { data } = $props();

	let f = $state({
		name: '',
		sport_id: '',
		owner_email: '',
		owner_first_name: '',
		owner_last_name: '',
		legal_name: '',
		contact_email: '',
		website: '',
		legal_address: '',
		legal_zip_code: '',
		legal_city: '',
		legal_country: '',
		vat_id: ''
	});
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let busy = $state(false);
	let job = $state<Provisioning | null>(null);

	// One key per form: a double click or a retry after a dropped connection never creates two clubs.
	let idempotencyKey = crypto.randomUUID();
	let timer: ReturnType<typeof setTimeout> | undefined;
	onDestroy(() => clearTimeout(timer));

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const body: Record<string, string> = {};
		for (const [key, value] of Object.entries(f)) if (value.trim()) body[key] = value.trim();
		const r = await platform<Provisioning>('POST', '/clubs', body, fetch, { 'Idempotency-Key': idempotencyKey });
		busy = false;
		if (!r.ok) {
			if (r.status !== 0) idempotencyKey = crypto.randomUUID(); // a rejected request is over; the corrected form is a new one
			fields = r.fields;
			error = Object.keys(r.fields).length ? 'Please fix the highlighted fields.' : (r.message ?? '');
			return;
		}
		job = r.data!;
		poll();
	}

	async function poll() {
		if (!job) return;
		const r = await platform<Provisioning>('GET', `/club-provisioning/${job.id}`);
		if (r.ok) job = r.data!;
		if (job.status === 'pending' || job.status === 'running') timer = setTimeout(poll, 1500);
		else if (job.status === 'succeeded' && job.club_id) timer = setTimeout(() => goto(`/clubs/${job!.club_id}`), 1200);
	}

	const building = $derived(job && (job.status === 'pending' || job.status === 'running'));
</script>

<svelte:head><title>New club · RosterChief</title></svelte:head>

<div>
	<a class="mb-2 inline-flex items-center gap-1.5 font-mono text-[11px] text-muted hover:text-ink" href="/clubs"><ArrowLeft size={12} aria-hidden="true" /> All clubs</a>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">New club</h1>
</div>

{#if job}
	<div class="card max-w-xl">
		<div class="card-body items-start gap-3">
			{#if building}
				<div class="card-title"><LoaderCircle size={18} class="animate-spin" aria-hidden="true" /> Setting up “{job.name}”</div>
				<p class="text-sm text-muted">Creating the club's database schema, default plan, domain and owner. This takes a few seconds.</p>
			{:else if job.status === 'succeeded'}
				<div class="card-title text-ok-text"><CircleCheck size={18} aria-hidden="true" /> “{job.name}” is ready</div>
				<p class="text-sm text-muted">Opening the club…</p>
				<a class="btn btn-primary btn-sm" href="/clubs/{job.club_id}">Open club</a>
			{:else}
				<Alert kind="error" title="Creation failed">{job.error || 'The club could not be created.'}</Alert>
				<button class="btn btn-outline btn-sm" onclick={() => (job = null)}>Back to the form</button>
			{/if}
		</div>
	</div>
{:else}
	<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
		{#if error}<Alert kind="error">{error}</Alert>{/if}

		<!-- One column up to very large screens, then club + owner on the left and legal details on the right. -->
		<div class="grid items-start gap-4 2xl:grid-cols-2">
		<div class="flex flex-col gap-4">
		<div class="card">
			<div class="card-body gap-4">
				<div class="card-title">Club</div>
				<div class="grid gap-4 sm:grid-cols-2">
					<Field label="Name" required maxlength={120} bind:value={f.name} error={fields.name} help="The web address is made from the name and can't change afterwards." />
					<div class="form-control w-full">
						<label class="label" for="sport"><span class="label-text">Sport</span></label>
						<select id="sport" class="select select-bordered w-full" bind:value={f.sport_id}>
							<option value="">Not set</option>
							{#each data.sports as sport (sport.id)}<option value={sport.id}>{sport.name}</option>{/each}
						</select>
						{#if fields.sport_id}<span class="mt-1 block text-xs text-club-dark">{fields.sport_id}</span>{/if}
					</div>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-body gap-4">
				<div class="card-title">Owner</div>
				<p class="-mt-1 text-sm text-muted">The club's first administrator. If this person has no account yet we create one and email them a link to choose a password; then their name is required.</p>
				<Field label="Email" type="email" required autocomplete="off" bind:value={f.owner_email} error={fields.owner_email} />
				<div class="grid gap-4 sm:grid-cols-2">
					<Field label="First name" autocomplete="off" bind:value={f.owner_first_name} error={fields.owner_first_name} />
					<Field label="Last name" autocomplete="off" bind:value={f.owner_last_name} error={fields.owner_last_name} />
				</div>
			</div>
		</div>
		</div>

		<div class="card">
			<div class="card-body gap-4">
				<div class="card-title">Legal &amp; contact <span class="font-body text-xs font-normal tracking-normal text-muted normal-case">optional, used on invoices</span></div>
				<div class="grid gap-4 sm:grid-cols-2">
					<Field label="Legal name" bind:value={f.legal_name} error={fields.legal_name} />
					<Field label="VAT number" bind:value={f.vat_id} error={fields.vat_id} />
					<Field label="Contact email" type="email" bind:value={f.contact_email} error={fields.contact_email} />
					<Field label="Website" type="url" placeholder="https://" bind:value={f.website} error={fields.website} />
				</div>
				<Field label="Address" bind:value={f.legal_address} error={fields.legal_address} />
				<div class="grid gap-4 sm:grid-cols-[10rem_1fr]">
					<Field label="Postal code" bind:value={f.legal_zip_code} error={fields.legal_zip_code} />
					<Field label="City" bind:value={f.legal_city} error={fields.legal_city} />
				</div>
				<div class="form-control w-full">
					<label class="label" for="country"><span class="label-text">Country</span></label>
					<select id="country" class="select select-bordered w-full" bind:value={f.legal_country}>
						<option value="">Not set</option>
						{#each data.countries as country (country.code)}<option value={country.code}>{country.name}</option>{/each}
					</select>
					{#if fields.legal_country}<span class="mt-1 block text-xs text-club-dark">{fields.legal_country}</span>{/if}
				</div>
			</div>
		</div>

		</div>

		<div class="flex gap-2">
			<button class="btn btn-primary" type="submit" disabled={busy || !f.name.trim() || !f.owner_email.trim()}>Create club</button>
			<a class="btn btn-outline" href="/clubs">Cancel</a>
		</div>
	</form>
{/if}
