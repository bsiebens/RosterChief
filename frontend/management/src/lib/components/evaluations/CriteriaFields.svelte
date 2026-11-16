<script lang="ts">
	import { ListChecks } from '@lucide/svelte';
	import { bySection, optionsOf, type FieldOut } from '#lib/forms.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * The fill-in form of a checklist: every active criterion as an input named `a.<key>` (a multichoice repeats the name),
	 * grouped under its section heading. `errors` are the API's per-criterion messages keyed by criterion key.
	 */
	let { fields, errors = {} }: { fields: FieldOut[]; errors?: Record<string, string> } = $props();
	const groups = $derived(bySection(fields.filter((f) => f.is_active).sort((a, b) => a.order - b.order)));
	const id = $props.id();
</script>

{#each groups as g, gi (gi)}
	<section class="grid gap-4">
		{#if g.section}<h3 class="flex items-center gap-2 border-b border-rule pb-1 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListChecks size={16} aria-hidden="true" /> {g.section}</h3>{/if}
		{#each g.items as f (f.id)}
			{@const fid = `${id}-${f.key}`}
			{@const err = errors[f.key]}
			<div class="form-control w-full">
				{#if f.field_type === 'checkbox'}
					<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox" name="a.{f.key}" value="true" /> {f.label}{#if f.required}<span class="text-error" aria-hidden="true">*</span>{/if}</label>
				{:else if f.field_type === 'choice' || f.field_type === 'multichoice'}
					<fieldset class="grid gap-1.5" data-invalid={err ? 'true' : undefined}>
						<legend class="label-text mb-1">{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</legend>
						<div class="flex flex-wrap gap-x-4 gap-y-1.5">
							{#each optionsOf(f) as o (o.value)}
								<label class="flex items-center gap-2 text-sm"><input type={f.field_type === 'choice' ? 'radio' : 'checkbox'} class={f.field_type === 'choice' ? 'radio' : 'checkbox'} name="a.{f.key}" value={o.value} /> {o.label}</label>
							{/each}
						</div>
					</fieldset>
				{:else}
					<label class="label" for={fid}><span class="label-text">{f.label}{#if f.required}<span class="text-error" aria-hidden="true"> *</span>{/if}</span></label>
					{#if f.field_type === 'textarea'}
						<textarea id={fid} name="a.{f.key}" class="textarea textarea-bordered min-h-20 w-full {err ? 'textarea-error' : ''}" aria-invalid={err ? 'true' : undefined}></textarea>
					{:else}
						<input id={fid} name="a.{f.key}" class="input input-bordered w-full {err ? 'input-error' : ''}" type={f.field_type === 'number' ? 'number' : f.field_type} step={f.field_type === 'number' ? 'any' : undefined} min={f.field_type === 'number' && f.min_value != null ? Number(f.min_value) : undefined} max={f.field_type === 'number' && f.max_value != null ? Number(f.max_value) : undefined} aria-invalid={err ? 'true' : undefined} />
					{/if}
				{/if}
				{#if err}<span class="mt-1 block text-xs text-error">{err}</span>{:else if f.help_text}<span class="label-text-alt mt-1 block">{f.help_text}</span>{/if}
			</div>
		{/each}
	</section>
{/each}
{#if !groups.length}<p class="text-sm text-muted">{t('eval.fill.no_criteria')}</p>{/if}
