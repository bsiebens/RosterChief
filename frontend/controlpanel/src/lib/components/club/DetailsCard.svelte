<script lang="ts">
	import { untrack } from 'svelte';
	import { Building2, Pencil } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubDetail, Country, Sport } from '#lib/clubs.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let { club, sports, countries, onchange }: { club: ClubDetail; sports: Sport[]; countries: Country[]; onchange: () => void } = $props();

	const initial = () => ({
		name: club.name,
		sport_id: club.sport?.id ?? '',
		legal_name: club.legal_name,
		vat_id: club.vat_id,
		contact_email: club.contact_email,
		website: club.website,
		legal_address: club.legal_address,
		legal_zip_code: club.legal_zip_code,
		legal_city: club.legal_city,
		legal_country: club.legal_country
	});

	let open = $state(false);
	let f = $state(initial());
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let saved = $state(false);
	let busy = $state(false);

	// The form always starts from what the server says; it is rebuilt each time the dialog opens.
	function edit() {
		f = initial();
		fields = {};
		error = '';
		open = true;
	}

	// Rebuild after a reload only while the dialog is closed (never under the user's typing).
	$effect(() => {
		const next = initial();
		untrack(() => {
			if (!open) f = next;
		});
	});

	async function save(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const base = initial() as Record<string, string>;
		const changes: Record<string, string | null> = {};
		for (const [key, value] of Object.entries(f) as [string, string][]) if (value !== base[key]) changes[key] = key === 'sport_id' ? value || null : value.trim();
		if (!Object.keys(changes).length) {
			open = false;
			busy = false;
			return;
		}
		const r = await platform('PATCH', `/clubs/${club.id}`, changes);
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = Object.keys(r.fields).length ? 'Please fix the highlighted fields.' : (r.message ?? '');
			return;
		}
		open = false;
		saved = true;
		onchange();
	}

	const rows = $derived<[string, string][]>([
		['Name', club.name],
		['Sport', club.sport?.name ?? ''],
		['Legal name', club.legal_name],
		['VAT number', club.vat_id],
		['Contact email', club.contact_email],
		['Website', club.website],
		['Address', [club.legal_address, [club.legal_zip_code, club.legal_city].filter(Boolean).join(' '), club.legal_country_name].filter(Boolean).join(', ')]
	]);
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			<Building2 size={16} aria-hidden="true" /> Details
			<button class="btn btn-outline btn-xs ml-auto" onclick={edit}><Pencil size={12} aria-hidden="true" /> Edit</button>
		</div>
		{#if saved}<Alert kind="success">Saved.</Alert>{/if}
		<dl>
			{#each rows as [label, value] (label)}
				<div class="flex items-baseline justify-between gap-4 py-1.5">
					<dt class="shrink-0 text-sm text-muted">{label}</dt>
					<dd class="text-right text-sm {value ? 'text-ink' : 'text-dim'}">{value || '—'}</dd>
				</div>
			{/each}
		</dl>
		<p class="font-mono text-[11px] text-muted">The slug <strong>{club.slug}</strong> and its database schema never change.</p>
	</div>
</div>

<Modal bind:open title="Edit club" wide>
	<form class="flex flex-col gap-4" onsubmit={save}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="grid gap-4 sm:grid-cols-2">
			<Field label="Name" required bind:value={f.name} error={fields.name} />
			<div class="form-control w-full">
				<label class="label" for="club-sport"><span class="label-text">Sport</span></label>
				<select id="club-sport" class="select select-bordered w-full" bind:value={f.sport_id}>
					<option value="">Not set</option>
					{#each sports as sport (sport.id)}<option value={sport.id}>{sport.name}</option>{/each}
				</select>
			</div>
			<Field label="Legal name" bind:value={f.legal_name} error={fields.legal_name} />
			<Field label="VAT number" bind:value={f.vat_id} error={fields.vat_id} />
			<Field label="Contact email" type="email" bind:value={f.contact_email} error={fields.contact_email} />
			<Field label="Website" type="url" placeholder="https://" bind:value={f.website} error={fields.website} />
		</div>
		<Field label="Address" bind:value={f.legal_address} error={fields.legal_address} />
		<div class="grid gap-4 sm:grid-cols-[10rem_1fr_1fr]">
			<Field label="Postal code" bind:value={f.legal_zip_code} error={fields.legal_zip_code} />
			<Field label="City" bind:value={f.legal_city} error={fields.legal_city} />
			<div class="form-control w-full">
				<label class="label" for="club-country"><span class="label-text">Country</span></label>
				<select id="club-country" class="select select-bordered w-full" bind:value={f.legal_country}>
					<option value="">Not set</option>
					{#each countries as country (country.code)}<option value={country.code}>{country.name}</option>{/each}
				</select>
				{#if fields.legal_country}<span class="mt-1 block text-xs text-club-dark">{fields.legal_country}</span>{/if}
			</div>
		</div>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy}>Save changes</button>
		</div>
	</form>
</Modal>
