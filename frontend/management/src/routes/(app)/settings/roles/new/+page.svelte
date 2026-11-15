<script lang="ts">
	import { enhance } from '$app/forms';
	import { ShieldCheck, ArrowLeft, Save, ClipboardList, Settings2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import PermissionPicker from '#lib/components/settings/PermissionPicker.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let name = $state(form?.values?.name ?? '');
	// svelte-ignore state_referenced_locally
	let description = $state(form?.values?.description ?? '');
	// svelte-ignore state_referenced_locally
	let selected = $state<string[]>(form?.values?.permissions ?? []);
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived((form?.fields ?? {}) as Record<string, string>);
	const nameError = $derived(fields.name || (tried && !name.trim() ? t('roles.err.name') : ''));
	const errorText = $derived(form?.message && !Object.keys(fields).some((k) => k !== '__all__') ? String(form.message) : '');

	usePageHeader(() => ({ title: t('roles.new.title'), context: t('roles.title'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/settings/roles"><ArrowLeft size={16} /> {t('roles.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('roles.new.title'))}</title></svelte:head>

<form
	method="POST"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!name.trim()) {
			tried = true;
			cancel();
			return;
		}
		busy = true;
		return async ({ update }) => {
			await update({ reset: false });
			busy = false;
		};
	}}>
	{#if errorText}<Alert kind="error" title={t('roles.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardList size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.details')}</h2>
				<div class="form-control">
					<label class="label" for="name"><span class="label-text">{t('roles.field.name')}</span></label>
					<input id="name" name="name" class="input input-bordered w-full {nameError ? 'input-error' : ''}" bind:value={name} maxlength="150" aria-invalid={nameError ? 'true' : undefined} />
					{#if nameError}<span class="mt-1 block text-xs text-error">{nameError}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="description"><span class="label-text">{t('roles.field.description')}</span></label>
					<input id="description" name="description" class="input input-bordered w-full" bind:value={description} maxlength="255" />
				</div>
			</section>
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ShieldCheck size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.permissions')}</h2>
				<p class="text-xs text-muted">{t('roles.card.permissions_desc')}</p>
				<PermissionPicker permissions={data.permissions} bind:selected />
			</section>
		</div>
		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Settings2 size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.actions')}</h2>
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('roles.create')}</button>
					<a class="btn btn-outline w-full" href="/settings/roles">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
