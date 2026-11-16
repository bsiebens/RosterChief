<script module lang="ts">
	export type LineDraft = { key: number; description: string; quantity: number; unit_price: string };
	let seq = 0;
	export const newLine = (from?: Partial<LineDraft>): LineDraft => ({ key: ++seq, description: '', quantity: 1, unit_price: '', ...from });
	/** The amount of a line as a number (0 while the field is empty or not a number). */
	export const lineTotal = (l: LineDraft) => (Number(l.unit_price) || 0) * (Number(l.quantity) || 0);
</script>

<script lang="ts">
	import { ListOrdered, NotebookPen, Plus, Trash2, UserRound } from '@lucide/svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import { money } from '#lib/charges.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * The fields of a charge invoice: who it is for, the lines (a description and an amount each), an optional due date and an internal note.
	 * The page owns the state (bindable) and posts it. `memberName` is set when editing a draft that already has a member.
	 */
	let {
		memberId = $bindable(''),
		memberName = '',
		lines = $bindable(),
		dueDate = $bindable(''),
		notes = $bindable(''),
		invoiceText = $bindable(''),
		errors = {},
		tried = false
	}: { memberId?: string; memberName?: string; lines: LineDraft[]; dueDate?: string; notes?: string; invoiceText?: string; errors?: Record<string, string>; tried?: boolean } = $props();

	let changing = $state(false);
	const showName = $derived(!!memberName && !!memberId && !changing);
	const total = $derived(lines.reduce((sum, l) => sum + lineTotal(l), 0));
	const remove = (key: number) => (lines = lines.length > 1 ? lines.filter((l) => l.key !== key) : lines);
	const lineError = (i: number) => errors[`lines.${i}`] ?? '';
</script>

<div class="grid content-start gap-5">
	<section class="card card-body card-hl gap-3" aria-labelledby="chg-member">
		<h2 id="chg-member" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><UserRound size={16} aria-hidden="true" /> {t('chg.form.member')}</h2>
		{#if showName}
			<div class="flex flex-wrap items-center gap-3">
				<span class="font-semibold text-ink">{memberName}</span>
				<button class="btn btn-outline btn-sm" type="button" onclick={() => ((changing = true), (memberId = ''))}>{t('chg.form.change')}</button>
			</div>
		{:else}
			<EntityPicker kind="member" name="member_id_picker" label={t('chg.form.member')} bind:value={memberId} error={errors.member_id ?? (tried && !memberId ? t('chg.form.member_required') : undefined)} />
		{/if}
	</section>

	<section class="card card-body card-hl gap-3" aria-labelledby="chg-lines">
		<header class="grid gap-0.5">
			<h2 id="chg-lines" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} aria-hidden="true" /> {t('chg.form.lines')}</h2>
			<p class="text-xs text-muted">{t('chg.form.lines_help')}</p>
		</header>
		{#if errors.lines}<p class="text-xs text-error">{errors.lines}</p>{/if}
		<div class="grid gap-3">
			{#each lines as l, i (l.key)}
				<div class="grid gap-2 rounded-lg border border-line p-3 md:grid-cols-[minmax(0,1fr)_5rem_8rem_7rem_auto] md:items-end">
					<label class="grid gap-1">
						<span class="text-xs font-semibold text-muted">{t('chg.form.description')}</span>
						<input class="input input-bordered w-full {lineError(i) ? 'input-error' : ''}" maxlength="255" placeholder={t('chg.form.description_ph')} bind:value={l.description} />
					</label>
					<label class="grid gap-1">
						<span class="text-xs font-semibold text-muted">{t('chg.form.qty')}</span>
						<input class="input input-bordered w-full" type="number" min="1" max="999" step="1" bind:value={l.quantity} />
					</label>
					<label class="grid gap-1">
						<span class="text-xs font-semibold text-muted">{t('chg.form.unit')}</span>
						<input class="input input-bordered w-full" type="number" min="0.01" step="0.01" inputmode="decimal" bind:value={l.unit_price} />
					</label>
					<div class="grid gap-1 md:text-right">
						<span class="text-xs font-semibold text-muted">{t('chg.form.line_total')}</span>
						<span class="flex h-12 items-center font-mono text-sm md:justify-end">{money(lineTotal(l))}</span>
					</div>
					<button class="btn btn-outline btn-error btn-square" type="button" aria-label={t('chg.form.remove_line')} title={t('chg.form.remove_line')} disabled={lines.length === 1} onclick={() => remove(l.key)}><Trash2 size={16} /></button>
					{#if lineError(i)}<p class="text-xs text-error md:col-span-5">{lineError(i)}</p>{/if}
				</div>
			{/each}
		</div>
		<div class="flex flex-wrap items-center justify-between gap-3">
			<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (lines = [...lines, newLine()])}><Plus size={14} /> {t('chg.form.add_line')}</button>
			<div class="text-right"><span class="text-sm text-muted">{t('chg.form.total')}</span> <span class="font-mono text-lg font-semibold text-ink">{money(total)}</span></div>
		</div>
		<p class="text-xs text-muted">{t('chg.form.no_vat')}</p>
	</section>

	<section class="card card-body card-hl gap-3" aria-labelledby="chg-details">
		<h2 id="chg-details" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><NotebookPen size={16} aria-hidden="true" /> {t('chg.form.details')}</h2>
		<div class="grid gap-3 sm:grid-cols-2">
			<Field label={t('chg.form.due_date')} name="due_date" type="date" bind:value={dueDate} error={errors.due_date} help={t('chg.form.due_help')} />
			<label class="form-control w-full sm:col-span-2">
				<span class="label"><span class="label-text">{t('chg.form.invoice_text')}</span></span>
				<textarea class="textarea textarea-bordered w-full {errors.invoice_text ? 'textarea-error' : ''}" rows="3" maxlength="2000" bind:value={invoiceText}></textarea>
				<span class="label-text-alt mt-1 block">{t('chg.form.invoice_text_help')}</span>
			</label>
			<label class="form-control w-full sm:col-span-2">
				<span class="label"><span class="label-text">{t('chg.form.notes')}</span></span>
				<textarea class="textarea textarea-bordered w-full" rows="3" maxlength="2000" bind:value={notes}></textarea>
				<span class="label-text-alt mt-1 block">{t('chg.form.notes_help')}</span>
			</label>
		</div>
	</section>
</div>
