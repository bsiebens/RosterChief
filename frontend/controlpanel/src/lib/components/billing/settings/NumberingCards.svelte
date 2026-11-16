<script lang="ts">
	// Automatic number format for invoices and credit notes, with a live (read-only) preview from the API.
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import type { CounterResets, IssuerSettings, NumberPatternPreview, NumberingPreview } from '#lib/billing.ts';

	let {
		current,
		invoicePattern = $bindable(''),
		creditPattern = $bindable(''),
		invoiceNext = $bindable(''),
		creditNext = $bindable(''),
		fields,
		canEdit
	}: { current: IssuerSettings; invoicePattern: string; creditPattern: string; invoiceNext: string; creditNext: string; fields: Record<string, string>; canEdit: boolean } = $props();

	const id = $props.id();
	let preview = $state<NumberingPreview | null>(null);
	let loadError = $state(false);
	let seq = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const inv = invoicePattern;
		const cn = creditPattern;
		clearTimeout(timer);
		const mine = ++seq;
		timer = setTimeout(async () => {
			const r = await platform<NumberingPreview>('POST', '/billing/numbering/preview', { invoice_number_pattern: inv, credit_note_number_pattern: cn });
			if (mine !== seq) return;
			untrack(() => {
				loadError = !r.ok;
				if (r.ok && r.data) preview = r.data;
			});
		}, 300);
		return () => clearTimeout(timer);
	});

	const resetText: Record<CounterResets, string> = { yearly: 'every year', monthly: 'every month', never: 'never' };
	const examples = ['INV-{YYYY}-{NNNNN}', '{YY}{MM}-{NNN}', 'FAC/{YYYY}/{NNNN}'];

	const cards = $derived([
		{ key: 'invoice', title: 'Invoice numbers', pattern: 'invoice_number_pattern', seqField: 'invoice_next_sequence', cur: current.invoice_current_sequence, next: current.invoice_next_number_preview, prev: preview?.invoice, who: 'invoice' },
		{ key: 'credit', title: 'Credit note numbers', pattern: 'credit_note_number_pattern', seqField: 'credit_note_next_sequence', cur: current.credit_note_current_sequence, next: current.credit_note_next_number_preview, prev: preview?.credit_note, who: 'credit note' }
	]);
</script>

{#snippet live(p: NumberPatternPreview | undefined)}
	<div class="min-h-[3.25rem] text-sm" aria-live="polite">
		{#if !p}
			<span class="text-muted">{loadError ? 'The preview is not available.' : 'Checking…'}</span>
		{:else if !p.valid}
			<span class="text-club-dark">{p.error}</span>
		{:else}
			<div>Example: <span class="font-mono">{p.example}</span></div>
			<div>Next number: <span class="font-mono">{p.next}</span></div>
			<div class="text-xs text-muted">Counter restarts: {p.resets ? resetText[p.resets] : ''}</div>
		{/if}
	</div>
{/snippet}

<div class="grid items-stretch gap-4 md:grid-cols-2">
	{#each cards as c (c.key)}
		{@const pat = c.key === 'invoice' ? invoicePattern : creditPattern}
		<fieldset class="card min-w-0" disabled={!canEdit}>
			<div class="card-body gap-3">
				<legend class="card-title float-left w-full">{c.title}</legend>
				<div class="form-control">
					<label class="label" for="{id}-{c.key}"><span class="label-text">Format</span></label>
					{#if c.key === 'invoice'}
						<input id="{id}-{c.key}" class="input input-bordered w-full {fields[c.pattern] ? 'input-error' : ''}" bind:value={invoicePattern} maxlength="40" spellcheck="false" autocomplete="off" aria-invalid={fields[c.pattern] ? 'true' : undefined} aria-describedby="{id}-{c.key}-e" />
					{:else}
						<input id="{id}-{c.key}" class="input input-bordered w-full {fields[c.pattern] ? 'input-error' : ''}" bind:value={creditPattern} maxlength="40" spellcheck="false" autocomplete="off" aria-invalid={fields[c.pattern] ? 'true' : undefined} aria-describedby="{id}-{c.key}-e" />
					{/if}
					<span id="{id}-{c.key}-e" class="mt-1 block text-xs text-club-dark">{fields[c.pattern] ?? ''}</span>
				</div>

				{@render live(c.prev)}

				<details class="text-sm">
					<summary class="cursor-pointer font-semibold">Placeholders and examples</summary>
					<div class="mt-2 flex flex-col gap-2">
						{#if preview}
							<ul class="space-y-0.5 text-xs">
								{#each preview.placeholders as ph (ph.token)}<li><span class="font-mono">{ph.token}</span> {ph.meaning}</li>{/each}
							</ul>
							<p class="text-xs text-muted">Also allowed: {preview.literal_characters}. At most {preview.max_length} characters; exactly one sequence placeholder.</p>
						{/if}
						{#if canEdit}
							<div class="flex flex-wrap gap-1.5">
								{#each examples as ex (ex)}
									<button type="button" class="btn btn-outline btn-xs font-mono" onclick={() => (c.key === 'invoice' ? (invoicePattern = ex) : (creditPattern = ex))}>{ex}</button>
								{/each}
							</div>
						{/if}
					</div>
				</details>

				<div class="rounded border border-rule bg-paper p-2 text-sm">
					Current counter: <span class="font-mono">{c.cur ?? '–'}</span>{#if c.next}, next number: <span class="font-mono">{c.next}</span>{/if}
					{#if current.invoice_counter_resets && c.key === 'invoice' || current.credit_note_counter_resets && c.key === 'credit'}
						<span class="block text-xs text-muted">Saved format restarts {resetText[(c.key === 'invoice' ? current.invoice_counter_resets : current.credit_note_counter_resets)!]}.</span>
					{/if}
				</div>

				<div class="form-control">
					<label class="label" for="{id}-{c.key}-seq"><span class="label-text">Set next number (optional)</span></label>
					{#if c.key === 'invoice'}
						<input id="{id}-{c.key}-seq" class="input input-bordered w-40 {fields[c.seqField] ? 'input-error' : ''}" type="number" step="1" min={(c.cur ?? 0) + 1} inputmode="numeric" bind:value={invoiceNext} aria-describedby="{id}-{c.key}-sd" />
					{:else}
						<input id="{id}-{c.key}-seq" class="input input-bordered w-40 {fields[c.seqField] ? 'input-error' : ''}" type="number" step="1" min={(c.cur ?? 0) + 1} inputmode="numeric" bind:value={creditNext} aria-describedby="{id}-{c.key}-sd" />
					{/if}
					<span id="{id}-{c.key}-sd" class="mt-1 block text-xs {fields[c.seqField] ? 'text-club-dark' : 'label-text-alt'}">{fields[c.seqField] ?? 'Only moves forward, e.g. when you take over from another system. Leave empty to keep counting.'}</span>
				</div>
			</div>
		</fieldset>
	{/each}
</div>
<p class="text-xs text-muted">Existing invoices and credit notes keep their numbers. A new format applies to documents created after saving. Changing it never repeats or skips a number: the counter continues. Credit notes are always numbered automatically.</p>
