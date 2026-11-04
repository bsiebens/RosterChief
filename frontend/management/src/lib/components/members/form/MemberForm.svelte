<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import PhotoPicker from '#lib/components/members/form/PhotoPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { LANGUAGES } from '#lib/members/detail-helpers.ts';
	import type { FormValues } from '#lib/members/detail-types.ts';

	/**
	 * Create/edit form. Posts to the page's default action, or to `action` (e.g. `?/save`) when the page has named actions (works without JS; `use:enhance` upgrades it).
	 * `fields` are per-field 422 messages, `message` a form-level error, `code` the API's machine code.
	 */
	let {
		initial,
		fields = {},
		message,
		code,
		cancelHref,
		profilePhotoUrl = null,
		playerPhotoUrl = null,
		id,
		action
	}: { action?: string; initial: FormValues; fields?: Record<string, string>; message?: string; code?: string; cancelHref: string; profilePhotoUrl?: string | null; playerPhotoUrl?: string | null; id?: string } = $props();

	let v = $state(untrack(() => ({ ...initial })));
	let busy = $state(false);
	const langId = $props.id();
	const formError = $derived(code === 'limit_exceeded' ? t('members.form.limit_exceeded') : message ? message : '');
</script>

{#snippet section(title: string)}
	<h2 class="mt-6 mb-3 border-b border-rule pb-1 font-display text-xs font-bold tracking-[.1em] text-muted uppercase first:mt-0">{title}</h2>
{/snippet}

<div class="card card-body">
	<form
		method="POST"
		{action}
		{id}
		enctype="multipart/form-data"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
			};
		}}
	>
		{#if formError}<div class="my-2"><Alert kind="error">{formError}</Alert></div>{/if}

		{@render section(t('members.form.section.photo'))}
		<div class="grid grid-cols-1 gap-6 md:grid-cols-2">
			<PhotoPicker name="profile_photo" label={t('members.form.photo')} help={t('members.form.photo_help')} url={profilePhotoUrl} first_name={v.first_name} last_name={v.last_name} error={fields.profile_photo} />
			<PhotoPicker name="player_photo" label={t('members.form.player_photo')} help={t('members.form.player_photo_help')} url={playerPhotoUrl} first_name={v.first_name} last_name={v.last_name} error={fields.player_photo} />
		</div>

		{@render section(t('members.form.section.identity'))}
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			<Field label={t('members.form.first_name')} name="first_name" bind:value={v.first_name} error={fields.first_name} required autocomplete="off" />
			<Field label={t('members.form.last_name')} name="last_name" bind:value={v.last_name} error={fields.last_name} required autocomplete="off" />
			<Field label={t('members.form.dob')} name="date_of_birth" type="date" bind:value={v.date_of_birth} error={fields.date_of_birth} />
			<Field label={t('members.form.license')} name="license_number" bind:value={v.license_number} error={fields.license_number} autocomplete="off" />
		</div>

		{@render section(t('members.form.section.contact'))}
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			<Field label={t('members.form.email')} name="email" type="email" bind:value={v.email} error={fields.email} autocomplete="off" />
			<div class="form-control w-full">
				<label class="label" for={langId}><span class="label-text">{t('members.form.language')}</span></label>
				<select id={langId} name="language" class="select select-bordered w-full {fields.language ? 'select-error' : ''}" bind:value={v.language}>
					<option value="">{t('members.form.language_default')}</option>
					{#each LANGUAGES as l (l)}<option value={l}>{l.toUpperCase()}</option>{/each}
				</select>
				{#if fields.language}<span class="mt-1 block text-xs text-error">{fields.language}</span>{/if}
			</div>
			<Field label={t('members.form.phone')} name="phone" type="tel" bind:value={v.phone} error={fields.phone} help={t('members.form.phone_help')} autocomplete="off" />
			<Field label={t('members.form.emergency_phone')} name="emergency_phone" type="tel" bind:value={v.emergency_phone} error={fields.emergency_phone} autocomplete="off" />
		</div>

		{@render section(t('members.form.section.privacy'))}
		<div class="form-control">
			<label class="label cursor-pointer justify-start gap-3">
				<input type="checkbox" name="photo_public_consent" class="checkbox" bind:checked={v.photo_public_consent} />
				<span class="label-text">{t('members.form.photo_consent')}</span>
			</label>
			{#if fields.photo_public_consent}<span class="block text-xs text-error">{fields.photo_public_consent}</span>{/if}
		</div>

		<div class="mt-2 flex items-center gap-2 pt-2">
			<a class="btn btn-outline gap-2" href={cancelHref}><ArrowLeft size={16} /> {t('members.form.cancel')}</a>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {busy ? t('members.form.saving') : t('members.form.save')}</button>
		</div>
	</form>
</div>
