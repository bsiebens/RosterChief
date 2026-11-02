<script lang="ts">
	import { ExternalLink, MapPin, Pencil, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubDetail, Country, HomeLocation } from '#lib/clubs.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';
	import LocationMap from './LocationMap.svelte';

	let { club, location, countries, canEdit, onchange }: { club: ClubDetail; location: HomeLocation | null; countries: Country[]; canEdit: boolean; onchange: () => void } = $props();

	let editOpen = $state(false);
	let removeOpen = $state(false);
	let f = $state({ name: '', address: '', zip_code: '', city: '', country: '', latitude: '', longitude: '' });
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let notice = $state('');
	let busy = $state(false);

	// The form always starts from what the server says (or the club's own country for a first location).
	function edit() {
		f = location
			? { name: location.name, address: location.address, zip_code: location.zip_code, city: location.city, country: location.country, latitude: location.latitude?.toString() ?? '', longitude: location.longitude?.toString() ?? '' }
			: { name: `${club.name} ground`, address: club.legal_address, zip_code: club.legal_zip_code, city: club.legal_city, country: club.legal_country, latitude: '', longitude: '' };
		fields = {};
		error = '';
		editOpen = true;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const num = (v: string) => (v.trim() === '' ? null : Number(v.trim().replace(',', '.')));
		const r = await platform('PUT', `/clubs/${club.id}/home-location`, { ...f, latitude: num(f.latitude), longitude: num(f.longitude) });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = Object.keys(r.fields).length ? 'Please fix the highlighted fields.' : (r.message ?? '');
			return;
		}
		editOpen = false;
		notice = location ? 'Home location updated.' : 'Home location set.';
		onchange();
	}

	async function remove() {
		busy = true;
		error = '';
		const r = await platform('DELETE', `/clubs/${club.id}/home-location`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The home location could not be removed.');
		removeOpen = false;
		notice = 'The location is no longer the home location.';
		onchange();
	}

	const mapsUrl = $derived(location ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([location.name, location.address, location.zip_code, location.city, location.country_name].filter(Boolean).join(', '))}` : '');
</script>

<div class="card @container">
	<div class="card-body gap-3">
		<div class="card-title">
			<MapPin size={16} aria-hidden="true" /> Home location
			{#if canEdit && location}
				<button class="btn btn-outline btn-xs ml-auto" type="button" onclick={edit}><Pencil size={12} aria-hidden="true" /> Edit</button>
			{/if}
		</div>
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
		{#if location}
			<div class="grid gap-4 @lg:grid-cols-2">
				<div class="order-2 @lg:order-1">
					<div class="font-display text-lg leading-none font-extrabold text-ink uppercase">{location.name}</div>
					<address class="mt-2 text-sm leading-relaxed text-slate not-italic">
						{location.address}<br />
						{location.zip_code} {location.city}<br />
						{location.country_name}
					</address>
				</div>
				<div class="order-1 h-56 @lg:order-2 @lg:h-auto"><LocationMap {location} /></div>
			</div>
		{:else}
			<div class="flex min-h-40 flex-col items-center justify-center gap-2 rounded border border-dashed border-edge bg-subhead text-center">
				<MapPin size={28} class="text-dim" aria-hidden="true" />
				<p class="font-mono text-[11px] text-muted">No home location set</p>
			</div>
		{/if}
		<div class="mt-auto flex flex-wrap items-center gap-2">
			{#if location}
				<a class="btn btn-outline btn-sm" href={mapsUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={13} aria-hidden="true" /> Open in Maps</a>
			{/if}
			{#if canEdit}
				{#if location}
					<button class="btn btn-outline btn-error btn-sm ml-auto" type="button" onclick={() => ((removeOpen = true), (error = ''))}><Trash2 size={13} aria-hidden="true" /> Delete</button>
				{:else}
					<button class="btn btn-outline btn-sm" type="button" onclick={edit}>Set home location</button>
				{/if}
			{/if}
		</div>
	</div>
</div>

<Modal bind:open={editOpen} title={location ? 'Edit home location' : 'Set home location'}>
	<form class="flex flex-col gap-4" onsubmit={save}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Name" required maxlength={255} bind:value={f.name} error={fields.name} />
		<Field label="Address" required maxlength={255} bind:value={f.address} error={fields.address} />
		<div class="grid gap-4 sm:grid-cols-[10rem_1fr]">
			<Field label="Postal code" required maxlength={255} bind:value={f.zip_code} error={fields.zip_code} />
			<Field label="City" required maxlength={255} bind:value={f.city} error={fields.city} />
		</div>
		<div class="form-control w-full">
			<label class="label" for="home-country"><span class="label-text">Country</span></label>
			<select id="home-country" class="select select-bordered w-full" required bind:value={f.country}>
				<option value="">Choose a country…</option>
				{#each countries as country (country.code)}<option value={country.code}>{country.name}</option>{/each}
			</select>
			{#if fields.country}<span class="mt-1 block text-xs text-club-dark">{fields.country}</span>{/if}
		</div>
		<div class="grid gap-4 sm:grid-cols-2">
			<Field label="Latitude (optional)" inputmode="decimal" placeholder="51.028700" bind:value={f.latitude} error={fields.latitude} />
			<Field label="Longitude (optional)" inputmode="decimal" placeholder="4.477300" bind:value={f.longitude} error={fields.longitude} />
		</div>
		<p class="-mt-2 text-xs text-muted">Only needed when the pin is in the wrong place: leave both empty to place it from the address. In Google or Apple Maps, right-click the spot to copy its coordinates.</p>
		{#if !location}<p class="text-xs text-muted">Pre-filled from the club's legal details. Change it if the ground is somewhere else.</p>{/if}
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (editOpen = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy}>Save</button>
		</div>
	</form>
</Modal>

<Modal bind:open={removeOpen} title="Remove home location?">
	<p class="text-sm"><strong>{location?.name}</strong> stops being the home location. The location itself is kept, so existing events keep pointing at it.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (removeOpen = false)}>Keep it</button>
		<button class="btn btn-error" disabled={busy} onclick={remove}>Remove</button>
	</div>
</Modal>
