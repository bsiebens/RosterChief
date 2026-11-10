<script lang="ts">
	import { choicesOf, type QuestionShape } from '#lib/requirements.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * The input for one question, by its type. Submits under the name `answer` (a multiple choice repeats the name; yes/no is the
	 * radio values `yes` / `no`). `value` is the current answer to start from; `invalid` marks the field for the error text below it.
	 */
	let { question, value = null, id, invalid = false, labelledby }: { question: QuestionShape; value?: unknown; id: string; invalid?: boolean; /** The id of the label that names the group (yes/no and multiple choice have no single input to point a `for` at). */ labelledby?: string } = $props();
	const type = $derived(question.question_type);
	const choices = $derived(choicesOf(question));
	const chosen = $derived(Array.isArray(value) ? value.map(String) : value === null || value === undefined ? [] : [String(value)]);
	const yesNo = $derived(value === true || value === 'true' ? 'yes' : value === false || value === 'false' ? 'no' : '');
	const cls = $derived(invalid ? 'input-error' : '');
</script>

{#if type === 'textarea'}
	<textarea {id} name="answer" class="textarea textarea-bordered w-full {invalid ? 'textarea-error' : ''}" rows="3" aria-invalid={invalid ? 'true' : undefined}>{value ?? ''}</textarea>
{:else if type === 'number'}
	<input {id} name="answer" type="number" step="any" min={question.min_value ?? undefined} max={question.max_value ?? undefined} class="input input-bordered w-full {cls}" value={value ?? ''} aria-invalid={invalid ? 'true' : undefined} />
{:else if type === 'date'}
	<input {id} name="answer" type="date" class="input input-bordered w-full {cls}" value={value ?? ''} aria-invalid={invalid ? 'true' : undefined} />
{:else if type === 'choice'}
	<select {id} name="answer" class="select select-bordered w-full {invalid ? 'select-error' : ''}" aria-invalid={invalid ? 'true' : undefined}>
		<option value="">{t('req.q.choose')}</option>
		{#each choices as o (o.value)}<option value={o.value} selected={chosen.includes(o.value)}>{o.label}</option>{/each}
	</select>
{:else if type === 'multichoice'}
	<fieldset class="grid gap-1.5" aria-labelledby={labelledby} data-invalid={invalid ? 'true' : undefined}>
		{#each choices as o (o.value)}
			<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" name="answer" value={o.value} checked={chosen.includes(o.value)} /> {o.label}</label>
		{/each}
	</fieldset>
{:else if type === 'yes_no'}
	<div class="flex gap-5 text-sm" role="radiogroup" aria-labelledby={labelledby}>
		<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="answer" value="yes" checked={yesNo === 'yes'} /> {t('common.yes')}</label>
		<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="answer" value="no" checked={yesNo === 'no'} /> {t('common.no')}</label>
	</div>
{:else}
	<input {id} name="answer" type="text" class="input input-bordered w-full {cls}" value={value ?? ''} maxlength="1000" aria-invalid={invalid ? 'true' : undefined} />
{/if}
