<script lang="ts">
	import { tick } from 'svelte';
	import { ListChecks, ArrowDown, ArrowUp, EyeOff, Plus, RotateCcw, Trash2, X } from '@lucide/svelte';
	import { blankField, blankOption, FIELD_TYPES, hasOptions, type FieldDraft, type FieldType } from '#lib/forms.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * The ordered list of a form's fields. Move up / down are plain buttons (and Alt + arrow keys inside a card), so
	 * ordering never needs a pointer. Removing a stored field only drops it from the list: the server deletes it, or keeps
	 * it as deactivated when it already has answers. `errors` are keyed `fields.<index>.<name>` as the API reports them.
	 */
	let { fields = $bindable(), disabled = false, errors = {}, tried = false, types = FIELD_TYPES, family = false }: { family?: boolean; fields: FieldDraft[]; disabled?: boolean; errors?: Record<string, string>; tried?: boolean; types?: FieldType[] } = $props();
	const base = $props.id();

	const err = (i: number, name: string) => errors[`fields.${i}.${name}`] ?? errors[`${i}.${name}`] ?? '';
	const labelErr = (f: FieldDraft, i: number) => err(i, 'label') || (tried && f.is_active && !f.label.trim() ? t('forms.err.field_label') : '');
	const optionsErr = (f: FieldDraft, i: number) => err(i, 'options') || (tried && f.is_active && hasOptions(f.field_type) && !f.options.some((o) => o.label.trim()) ? t('forms.err.options_required') : '');

	async function move(i: number, d: -1 | 1, focus: string) {
		const j = i + d;
		if (j < 0 || j >= fields.length) return;
		const next = [...fields];
		[next[i], next[j]] = [next[j], next[i]];
		fields = next;
		await tick();
		const el = document.getElementById(`${base}-${next[j].uid}-${focus}`) as HTMLElement | null;
		(el && !(el as HTMLButtonElement).disabled ? el : document.getElementById(`${base}-${next[j].uid}-card`))?.focus();
	}
	function remove(i: number) {
		fields = fields.filter((_, k) => k !== i);
	}
	function add() {
		const f = blankField();
		fields = [...fields, f];
		tick().then(() => document.getElementById(`${base}-${f.uid}-label`)?.focus());
	}
	function setType(f: FieldDraft, type: FieldDraft['field_type']) {
		f.field_type = type;
		if (family && !f.id) f.visible_to_family = type !== 'text' && type !== 'textarea';
		if (hasOptions(type) && !f.options.length) f.options = [blankOption(), blankOption()];
	}
	async function moveOption(f: FieldDraft, i: number, d: -1 | 1) {
		const j = i + d;
		if (j < 0 || j >= f.options.length) return;
		const next = [...f.options];
		[next[i], next[j]] = [next[j], next[i]];
		f.options = next;
		await tick();
		document.getElementById(`${base}-${f.uid}-opt-${next[j].uid}-${d < 0 ? 'up' : 'down'}`)?.focus();
	}
	function addOption(f: FieldDraft) {
		const o = blankOption();
		f.options = [...f.options, o];
		tick().then(() => document.getElementById(`${base}-${f.uid}-opt-${o.uid}`)?.focus());
	}
	function onKey(e: KeyboardEvent, i: number) {
		if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
		e.preventDefault();
		move(i, e.key === 'ArrowUp' ? -1 : 1, 'card');
	}
</script>

<div class="grid gap-3">
	{#if !fields.length}
		<p class="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">{t('forms.fields.empty')}</p>
	{/if}
	<p class="text-xs text-muted">{t('forms.fields.keyboard')}</p>
	<ol class="grid gap-3">
		{#each fields as f, i (f.uid)}
			<li>
				<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
				<fieldset
					id="{base}-{f.uid}-card"
					tabindex="-1"
					onkeydown={(e) => onKey(e, i)}
					{disabled}
					class="rounded-lg border bg-white p-4 outline-none focus-visible:ring-2 focus-visible:ring-club {f.is_active ? 'border-line' : 'border-dashed border-line bg-surface opacity-80'}">
					<legend class="sr-only">{t('forms.field.n', { n: i + 1, label: f.label || t('forms.field.untitled') })}</legend>
					<div class="mb-3 flex flex-wrap items-center gap-2">
						<span class="grid size-7 place-items-center rounded-full bg-primary font-mono text-xs font-bold text-primary-content" aria-hidden="true">{i + 1}</span>
						<span class="min-w-0 flex-1 truncate font-semibold text-ink">{f.label || t('forms.field.untitled')}</span>
						{#if !f.is_active}<span class="badge badge-sm badge-warning gap-1"><EyeOff size={11} aria-hidden="true" /> {t('forms.field.inactive')}</span>{/if}
						{#if f.required && f.is_active}<span class="badge badge-sm">{t('forms.field.required_badge')}</span>{/if}
						<div class="flex items-center gap-1">
							<button id="{base}-{f.uid}-up" type="button" class="btn btn-outline btn-xs btn-square" disabled={disabled || i === 0} aria-label={t('forms.field.up', { n: i + 1 })} onclick={() => move(i, -1, 'up')}><ArrowUp size={14} /></button>
							<button id="{base}-{f.uid}-down" type="button" class="btn btn-outline btn-xs btn-square" disabled={disabled || i === fields.length - 1} aria-label={t('forms.field.down', { n: i + 1 })} onclick={() => move(i, 1, 'down')}><ArrowDown size={14} /></button>
							{#if f.is_active}
								<button type="button" class="btn btn-outline btn-error btn-xs btn-square" aria-label={t('forms.field.remove', { n: i + 1 })} onclick={() => remove(i)}><Trash2 size={14} /></button>
							{:else}
								<button type="button" class="btn btn-outline btn-xs gap-1" onclick={() => (f.is_active = true)}><RotateCcw size={13} /> {t('forms.field.reactivate')}</button>
								<button type="button" class="btn btn-outline btn-error btn-xs btn-square" aria-label={t('forms.field.remove', { n: i + 1 })} onclick={() => remove(i)}><Trash2 size={14} /></button>
							{/if}
						</div>
					</div>
					{#if !f.is_active}<p class="mb-3 text-xs text-muted">{t('forms.field.inactive_help')}</p>{/if}

					<div class="grid gap-3 md:grid-cols-2">
						<div class="form-control">
							<label class="label" for="{base}-{f.uid}-label"><span class="label-text">{t('forms.field.label')}</span></label>
							<input id="{base}-{f.uid}-label" class="input input-bordered w-full {labelErr(f, i) ? 'input-error' : ''}" bind:value={f.label} maxlength="255" aria-invalid={labelErr(f, i) ? 'true' : undefined} aria-describedby={labelErr(f, i) ? `${base}-${f.uid}-label-err` : undefined} />
							{#if labelErr(f, i)}<span id="{base}-{f.uid}-label-err" class="mt-1 block text-xs text-error">{labelErr(f, i)}</span>{/if}
						</div>
						<div class="form-control">
							<label class="label" for="{base}-{f.uid}-type"><span class="label-text">{t('forms.field.type')}</span></label>
							<select id="{base}-{f.uid}-type" class="select select-bordered w-full" value={f.field_type} onchange={(e) => setType(f, e.currentTarget.value as FieldDraft['field_type'])}>
								{#each types as ft (ft)}<option value={ft}>{t(`forms.type.${ft}`)}</option>{/each}
							</select>
						</div>
						<div class="form-control">
							<label class="label" for="{base}-{f.uid}-help"><span class="label-text">{t('forms.field.help')}</span></label>
							<input id="{base}-{f.uid}-help" class="input input-bordered w-full" bind:value={f.help_text} />
						</div>
						<div class="form-control">
							<label class="label" for="{base}-{f.uid}-section"><span class="label-text">{t('forms.field.section')}</span></label>
							<input id="{base}-{f.uid}-section" class="input input-bordered w-full" bind:value={f.section} maxlength="120" aria-describedby="{base}-{f.uid}-section-help" />
							<span id="{base}-{f.uid}-section-help" class="label-text-alt mt-1 block">{t('forms.field.section_help')}</span>
						</div>
					</div>
					{#if f.field_type === 'number'}
						<div class="mt-3 grid gap-3 sm:grid-cols-2">
							<div class="form-control">
								<label class="label" for="{base}-{f.uid}-min"><span class="label-text">{t('forms.field.min')}</span></label>
								<input id="{base}-{f.uid}-min" class="input input-bordered w-full {err(i, 'min_value') ? 'input-error' : ''}" type="number" step="any" bind:value={f.min} placeholder={t('forms.field.no_limit')} />
								{#if err(i, 'min_value')}<span class="mt-1 block text-xs text-error">{err(i, 'min_value')}</span>{/if}
							</div>
							<div class="form-control">
								<label class="label" for="{base}-{f.uid}-max"><span class="label-text">{t('forms.field.max')}</span></label>
								<input id="{base}-{f.uid}-max" class="input input-bordered w-full {err(i, 'max_value') ? 'input-error' : ''}" type="number" step="any" bind:value={f.max} placeholder={t('forms.field.no_limit')} />
								{#if err(i, 'max_value')}<span class="mt-1 block text-xs text-error">{err(i, 'max_value')}</span>{/if}
							</div>
						</div>
					{/if}
					<label class="mt-3 flex w-fit cursor-pointer items-center gap-2 text-sm">
						<input type="checkbox" class="checkbox checkbox-sm" bind:checked={f.required} />
						{t('forms.field.required')}
					</label>
					{#if family}
						<label class="mt-2 flex w-fit cursor-pointer items-center gap-2 text-sm">
							<input type="checkbox" class="checkbox checkbox-sm" bind:checked={f.visible_to_family} />
							{t('eval.field.family')}
						</label>
						<p class="text-xs text-muted">{t('eval.field.family_help')}</p>
					{/if}

					{#if hasOptions(f.field_type)}
						<div class="mt-4 grid gap-2 border-t border-rule pt-3" role="group" aria-labelledby="{base}-{f.uid}-opts">
							<h3 id="{base}-{f.uid}-opts" class="font-display text-xs font-bold tracking-wide text-ink uppercase flex items-center gap-2"><ListChecks size={16} class="shrink-0" aria-hidden="true" /> {t('forms.options')}</h3>
							<ul class="grid gap-2">
								{#each f.options as o, k (o.uid)}
									<li class="flex items-center gap-1.5">
										<input id="{base}-{f.uid}-opt-{o.uid}" class="input input-bordered input-sm min-w-0 flex-1" bind:value={o.label} aria-label={t('forms.option.label', { n: k + 1 })} maxlength="200" />
										<button id="{base}-{f.uid}-opt-{o.uid}-up" type="button" class="btn btn-outline btn-xs btn-square" disabled={k === 0} aria-label={t('forms.option.up', { n: k + 1 })} onclick={() => moveOption(f, k, -1)}><ArrowUp size={13} /></button>
										<button id="{base}-{f.uid}-opt-{o.uid}-down" type="button" class="btn btn-outline btn-xs btn-square" disabled={k === f.options.length - 1} aria-label={t('forms.option.down', { n: k + 1 })} onclick={() => moveOption(f, k, 1)}><ArrowDown size={13} /></button>
										<button type="button" class="btn btn-ghost btn-xs btn-square" aria-label={t('forms.option.remove', { n: k + 1 })} onclick={() => (f.options = f.options.filter((x) => x.uid !== o.uid))}><X size={14} /></button>
									</li>
								{/each}
							</ul>
							<button type="button" class="btn btn-outline btn-xs w-fit gap-1" onclick={() => addOption(f)}><Plus size={13} /> {t('forms.option.add')}</button>
							{#if optionsErr(f, i)}<span class="block text-xs text-error">{optionsErr(f, i)}</span>{/if}
						</div>
					{/if}
				</fieldset>
			</li>
		{/each}
	</ol>
	<button type="button" class="btn btn-outline w-fit gap-2" {disabled} onclick={add}><Plus size={16} /> {t('forms.field.add')}</button>
</div>
