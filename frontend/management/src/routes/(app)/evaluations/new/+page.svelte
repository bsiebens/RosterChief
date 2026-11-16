<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { form } = $props();
	// svelte-ignore state_referenced_locally
	let name = $state(form?.values?.name ?? '');
	// svelte-ignore state_referenced_locally
	let description = $state(form?.values?.description ?? '');
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived((form?.fields ?? {}) as Record<string, string>);
	const nameError = $derived(fields.name || (tried && !name.trim() ? t('eval.err.name') : ''));
	const errorText = $derived(form?.message && !Object.keys(fields).some((k) => k !== '__all__') ? String(form.message) : '');

	usePageHeader(() => ({ title: t('eval.new.title'), context: t('eval.title'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/evaluations"><ArrowLeft size={16} /> {t('eval.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eval.new.title'))}</title></svelte:head>

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
	{#if errorText}<Alert kind="error" title={t('eval.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<section class="card card-body gap-3 xl:col-span-2">
			<p class="text-sm text-muted">{t('eval.new.next')}</p>
			<div class="form-control">
				<label class="label" for="name"><span class="label-text">{t('eval.field.name')}</span></label>
				<input id="name" name="name" class="input input-bordered w-full {nameError ? 'input-error' : ''}" bind:value={name} maxlength="255" aria-invalid={nameError ? 'true' : undefined} />
				{#if nameError}<span class="mt-1 block text-xs text-error">{nameError}</span>{/if}
			</div>
			<div class="form-control">
				<label class="label" for="description"><span class="label-text">{t('eval.field.description')}</span></label>
				<textarea id="description" name="description" class="textarea textarea-bordered w-full" rows="3" bind:value={description}></textarea>
				<span class="label-text-alt mt-1 block">{t('eval.field.description_help')}</span>
			</div>
		</section>
		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('eval.new.create')}</button>
					<a class="btn btn-outline w-full" href="/evaluations">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
