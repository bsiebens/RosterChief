<script lang="ts">
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { FormIn } from '#lib/forms.ts';

	/** Title, description and the two switches of a form (shared by the add page and the editor). */
	let { v = $bindable(), fields = {}, tried = false }: { v: FormIn; fields?: Record<string, string>; tried?: boolean } = $props();
	const uid = $props.id();
	const titleErr = $derived(fields.title || (tried && !v.title.trim() ? t('forms.err.title_required') : ''));
</script>

<div class="grid gap-4">
	<Field label={t('forms.f.title')} bind:value={v.title} error={titleErr} required maxlength={255} />
	<div class="form-control w-full">
		<label class="label" for="{uid}-desc"><span class="label-text">{t('forms.f.description')}</span></label>
		<textarea id="{uid}-desc" class="textarea textarea-bordered min-h-24 w-full" bind:value={v.description} aria-describedby="{uid}-desc-help"></textarea>
		<span id="{uid}-desc-help" class="label-text-alt mt-1 block {fields.description ? 'text-error' : ''}">{fields.description || t('forms.f.description_help')}</span>
	</div>
	<div class="grid gap-4 md:grid-cols-2">
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.login_required} />
			<span class="grid gap-0.5">
				<span class="text-sm font-semibold text-ink">{t('forms.f.login_required')}</span>
				<span class="text-xs text-muted">{t('forms.f.login_required_help')}</span>
			</span>
		</label>
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.is_active} />
			<span class="grid gap-0.5">
				<span class="text-sm font-semibold text-ink">{t('forms.f.is_active')}</span>
				<span class="text-xs text-muted">{t('forms.f.is_active_help')}</span>
			</span>
		</label>
	</div>
</div>
