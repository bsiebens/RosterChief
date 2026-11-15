<script lang="ts">
	import { ClipboardList, ListChecks, Paperclip } from '@lucide/svelte';
	import { bySection, hasOptions, optionsOf, type FieldDraft, type FieldOut } from '#lib/forms.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/** Read-only "what recipients will see": every field type, rendered as disabled controls. Inactive fields are left out. */
	type P = { uid: string; label: string; field_type: FieldOut['field_type']; required: boolean; help_text: string; section: string; options: { value: string; label: string }[] };
	let { title, description = '', fields }: { title: string; description?: string; fields: FieldDraft[] } = $props();
	const items = $derived(
		fields
			.filter((f) => f.is_active)
			.map((f): P => ({ uid: f.uid, label: f.label || t('forms.field.untitled'), field_type: f.field_type, required: f.required, help_text: f.help_text, section: f.section, options: hasOptions(f.field_type) ? f.options.filter((o) => o.label.trim()).map((o) => ({ value: o.uid, label: o.label })) : [] }))
	);
	const groups = $derived(bySection(items));
	const id = $props.id();
</script>

<div class="card card-body gap-5">
	<header class="grid gap-1">
		<h2 class="flex items-center gap-2 font-display text-2xl font-extrabold tracking-wide text-ink uppercase"><ClipboardList size={20} class="shrink-0" aria-hidden="true" /> {title || t('forms.untitled')}</h2>
		{#if description}<p class="text-sm whitespace-pre-line text-muted">{description}</p>{/if}
	</header>
	{#if !items.length}<p class="text-sm text-muted">{t('forms.preview.empty')}</p>{/if}
	{#each groups as g, gi (gi)}
		<section class="grid gap-4">
			{#if g.section}<h3 class="flex items-center gap-2 border-b border-rule pb-1 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListChecks size={16} aria-hidden="true" /> {g.section}</h3>{/if}
			{#each g.items as f (f.uid)}
				{@const fid = `${id}-${f.uid}`}
				<div class="form-control w-full">
					{#if f.field_type === 'checkbox'}
						<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox" disabled /> {f.label}{#if f.required}<span class="text-error" aria-hidden="true">*</span>{/if}</label>
					{:else if f.field_type === 'choice' || f.field_type === 'multichoice'}
						<fieldset class="grid gap-1.5">
							<legend class="label-text mb-1">{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</legend>
							{#each f.options as o (o.value)}
								<label class="flex items-center gap-2 text-sm"><input type={f.field_type === 'choice' ? 'radio' : 'checkbox'} class={f.field_type === 'choice' ? 'radio' : 'checkbox'} disabled /> {o.label}</label>
							{/each}
						</fieldset>
					{:else}
						<label class="label" for={fid}><span class="label-text">{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</span></label>
						{#if f.field_type === 'textarea'}
							<textarea id={fid} class="textarea textarea-bordered min-h-20 w-full" disabled></textarea>
						{:else if f.field_type === 'file'}
							<div id={fid} class="flex items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2 text-sm text-muted"><Paperclip size={15} aria-hidden="true" /> {t('forms.preview.file')}</div>
						{:else}
							<input id={fid} class="input input-bordered w-full" type={f.field_type} disabled />
						{/if}
					{/if}
					{#if f.help_text}<span class="label-text-alt mt-1 block">{f.help_text}</span>{/if}
				</div>
			{/each}
		</section>
	{/each}
</div>
