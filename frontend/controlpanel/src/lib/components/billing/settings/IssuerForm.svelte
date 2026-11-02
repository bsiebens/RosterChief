<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { IssuerSettings } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	type Country = { code: string; name: string };
	let { settings, countries, canEdit, onsaved }: { settings: IssuerSettings; countries: Country[]; canEdit: boolean; onsaved: (s: IssuerSettings) => void } = $props();

	const id = $props.id();
	const blank = (s: IssuerSettings) => ({ ...s });
	let f = $state<IssuerSettings>(untrack(() => blank(settings)));
	let archiveDays = $state(String(untrack(() => settings.archive_after_days)));
	let busy = $state(false);
	let error = $state('');
	let saved = $state(false);
	let fields = $state<Record<string, string>>({});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		saved = false;
		fields = {};
		const rate = String(f.vat_rate).trim().replace(',', '.');
		if (!/^\d+(\.\d{1,2})?$/.test(rate) || Number(rate) > 100) {
			fields.vat_rate = 'Enter a percentage between 0 and 100 with at most two decimals.';
			return;
		}
		const days = archiveDays.trim();
		if (!/^\d+$/.test(days) || Number(days) > 365) {
			fields.archive_after_days = 'Enter a whole number of days between 0 and 365.';
			return;
		}
		busy = true;
		const r = await platform<IssuerSettings>('PUT', '/billing/settings', { ...f, vat_rate: rate, archive_after_days: Number(days) });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			return (error = r.message ?? 'The settings could not be saved.');
		}
		archiveDays = String(r.data!.archive_after_days);
		f = blank(r.data!); // the server normalises (IBAN without spaces, upper case, 2-decimal rate)
		onsaved(r.data!);
		saved = true;
	}
</script>

<form class="flex flex-col gap-4" onsubmit={submit} novalidate aria-label="Issuer details and VAT">
	{#if !canEdit}<Alert kind="info">You can see the invoicing settings, but only superusers can change them.</Alert>{/if}
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	{#if saved}<Alert kind="success">Saved. Invoices issued from now on use these details.</Alert>{/if}
	{#if !f.country}
		<Alert kind="warning" title="Country is not set">Without the issuer's country, reverse charge cannot be applied: clubs with a VAT number in another EU country are charged VAT. Choose a country below.</Alert>
	{/if}

	<fieldset class="card" disabled={!canEdit || busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Issuer</legend>
			<div class="grid gap-3 sm:grid-cols-2">
				<Field label="Legal name" bind:value={f.legal_name} error={fields.legal_name} autocomplete="organization" />
				<Field label="VAT number" bind:value={f.vat_id} error={fields.vat_id} mono help="For example BE0123456789." />
				<Field label="Address" bind:value={f.address} error={fields.address} autocomplete="street-address" />
				<div class="grid grid-cols-[8rem_1fr] gap-3">
					<Field label="Postal code" bind:value={f.zip_code} error={fields.zip_code} autocomplete="postal-code" />
					<Field label="City" bind:value={f.city} error={fields.city} autocomplete="address-level2" />
				</div>
				<div class="form-control w-full">
					<label class="label" for="{id}-country"><span class="label-text">Country</span></label>
					<select id="{id}-country" class="select select-bordered w-full {fields.country ? 'select-error' : ''}" bind:value={f.country} aria-invalid={fields.country ? 'true' : undefined} aria-describedby="{id}-country-d">
						<option value="">Not set</option>
						{#each countries as c (c.code)}<option value={c.code}>{c.name}</option>{/each}
					</select>
					<span id="{id}-country-d" class="mt-1 block text-xs {fields.country ? 'text-club-dark' : 'label-text-alt'}">{fields.country ?? 'Used to decide when reverse charge applies.'}</span>
				</div>
				<Field label="Contact e-mail" type="email" bind:value={f.contact_email} error={fields.contact_email} autocomplete="email" />
				<Field label="Website" bind:value={f.website} error={fields.website} autocomplete="url" />
			</div>
		</div>
	</fieldset>

	<fieldset class="card" disabled={!canEdit || busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Payment</legend>
			<div class="grid gap-3 sm:grid-cols-2">
				<Field label="IBAN" bind:value={f.iban} error={fields.iban} mono help="Spaces are removed and letters upper-cased when saved." />
				<Field label="BIC" bind:value={f.bic} error={fields.bic} mono />
			</div>
			<div class="form-control">
				<label class="label" for="{id}-terms"><span class="label-text">Payment terms</span></label>
				<textarea id="{id}-terms" class="textarea textarea-bordered w-full" rows="3" bind:value={f.payment_terms}></textarea>
			</div>
			<div class="form-control">
				<label class="label" for="{id}-footer"><span class="label-text">Invoice footer</span></label>
				<textarea id="{id}-footer" class="textarea textarea-bordered w-full" rows="3" bind:value={f.invoice_footer}></textarea>
			</div>
		</div>
	</fieldset>

	<fieldset class="card" disabled={!canEdit || busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">VAT</legend>
			<div class="max-w-xs">
				<Field label="VAT rate (%)" bind:value={f.vat_rate} error={fields.vat_rate} mono inputmode="decimal" help="Between 0 and 100, up to two decimals. 0 means no VAT." />
			</div>
		</div>
	</fieldset>

	<fieldset class="card" disabled={!canEdit || busy}>
		<div class="card-body gap-3">
			<legend class="card-title float-left w-full">Overdue clubs</legend>
			<div class="max-w-xs">
				<Field label="Days between downgrade and archive" type="number" bind:value={archiveDays} error={fields.archive_after_days} mono inputmode="numeric" help="An unpaid club is first moved to the free plan. It is archived this many days later (0 = immediately), only if the club has auto-archive on." />
			</div>
		</div>
	</fieldset>

	{#if canEdit}
		<div class="flex justify-end"><button type="submit" class="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save settings'}</button></div>
	{/if}
</form>
