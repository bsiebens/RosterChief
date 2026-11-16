<script lang="ts">
	import { untrack } from 'svelte';
	import { Hash, Lock, RotateCcw } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { browserApi, type NumberingChanges, type NumberingPlaceholder, type NumberingPreview, type NumberingState } from '#lib/shop-orders.ts';

	/**
	 * Numbering schemes of the shop's documents: a pattern per type (literal text + {YYYY} {YY} {MM} and one {NNNNN}-style sequence),
	 * a live preview of the next number, and an optional "next sequence value" (forward only). `changes` holds only what was edited,
	 * in the shape the settings API takes; it empties again when the saved settings come back.
	 */
	let { rows, placeholders, errors = {}, canEditInvoice = true, changes = $bindable({}) }: { rows: NumberingState[]; placeholders: NumberingPlaceholder[]; errors?: Record<string, string>; canEditInvoice?: boolean; changes?: NumberingChanges } = $props();
	/** Invoice numbers must run without surprises: only whoever manages the whole shop may change them (the API refuses anyone else). */
	const locked = (kind: string) => kind === 'invoice' && !canEditInvoice;

	// svelte-ignore state_referenced_locally
	let patterns = $state<Record<string, string>>(Object.fromEntries(rows.map((r) => [r.kind, r.pattern])));
	// svelte-ignore state_referenced_locally
	let nexts = $state<Record<string, string>>(Object.fromEntries(rows.map((r) => [r.kind, ''])));
	let previews = $state<Record<string, NumberingPreview | undefined>>({});

	// The saved numbering came back (after a save): start from it again.
	$effect(() => {
		const stored = rows;
		untrack(() => {
			patterns = Object.fromEntries(stored.map((r) => [r.kind, r.pattern]));
			nexts = Object.fromEntries(stored.map((r) => [r.kind, '']));
			previews = {};
		});
	});

	const computed = $derived(
		Object.fromEntries(
			rows.flatMap((r) => {
				const c: { pattern?: string; next?: number } = {};
				const p = (patterns[r.kind] ?? r.pattern).trim();
				if (p !== r.pattern) c.pattern = p;
				const n = Number.parseInt(nexts[r.kind] ?? '', 10);
				if ((nexts[r.kind] ?? '').trim() && n > 0) c.next = n;
				return Object.keys(c).length ? [[r.kind, c]] : [];
			})
		) as NumberingChanges
	);
	$effect(() => {
		changes = computed;
	});

	const timers: Record<string, ReturnType<typeof setTimeout>> = {};
	const onPattern = (kind: string) => {
		clearTimeout(timers[kind]);
		const saved = rows.find((r) => r.kind === kind)?.pattern ?? '';
		const typed = patterns[kind] ?? '';
		if (typed.trim() === saved) {
			previews[kind] = undefined;
			return;
		}
		timers[kind] = setTimeout(async () => {
			const res = await browserApi<NumberingPreview>('POST', '/shop/settings/numbering-preview', { kind, pattern: typed });
			if (res.ok && res.data && (patterns[kind] ?? '') === typed) previews[kind] = res.data;
		}, 350);
	};
	const useDefault = (r: NumberingState) => {
		patterns[r.kind] = r.default_pattern;
		onPattern(r.kind);
	};
	const resetsText = (resets: string, scope: string) => (resets === 'never' ? t('shop.set.num.resets_never') : t(`shop.set.num.resets_${resets}`, { scope }));
</script>

<section class="card card-body gap-3" aria-labelledby="num-h">
	<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
		<h2 id="num-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Hash size={16} aria-hidden="true" /> {t('shop.set.card.numbering')}</h2>
		<p class="text-xs text-muted">{t('shop.set.num.help')}</p>
	</div>

	<details class="rounded-lg border border-line bg-surface px-3 py-2 text-xs">
		<summary class="cursor-pointer font-semibold text-ink">{t('shop.set.num.placeholders')}</summary>
		<ul class="mt-2 grid gap-x-6 gap-y-1 text-muted sm:grid-cols-2">
			{#each placeholders as p (p.token)}<li><span class="font-mono font-semibold text-ink">{p.token}</span><span class="ml-2">{p.meaning}</span></li>{/each}
		</ul>
	</details>

	<div class="hidden gap-3 px-1 text-xs font-semibold tracking-wide text-muted uppercase md:grid md:grid-cols-[8.5rem_minmax(0,1fr)_7.5rem]">
		<span>{t('shop.set.num.col_doc')}</span><span>{t('shop.set.num.col_pattern')}</span><span>{t('shop.set.num.col_next')}</span>
	</div>
	<ul class="divide-y divide-line border-y border-line">
		{#each rows as r (r.kind)}
			{@const preview = previews[r.kind]}
			{@const patErr = errors[`numbering.${r.kind}.pattern`] || (preview && !preview.valid ? preview.message : '')}
			{@const nextErr = errors[`numbering.${r.kind}.next`]}
			{@const off = locked(r.kind)}
			<li class="grid items-start gap-x-3 gap-y-1.5 py-3 md:grid-cols-[8.5rem_minmax(0,1fr)_7.5rem]">
				<label class="flex items-center gap-1.5 text-sm font-semibold text-ink md:h-9" for="num-{r.kind}">
					{t(`shop.set.num.kind.${r.kind}`)}
					{#if off}<Lock size={12} class="text-muted" aria-hidden="true" />{/if}
				</label>
				<div class="min-w-0">
					<div class="flex gap-2">
						<input id="num-{r.kind}" class="input input-bordered w-full font-mono" bind:value={patterns[r.kind]} oninput={() => onPattern(r.kind)} maxlength="40" autocomplete="off" spellcheck="false" disabled={off} aria-invalid={patErr ? 'true' : undefined} aria-describedby="num-{r.kind}-info" />
						{#if !off && (patterns[r.kind] ?? r.pattern).trim() !== r.default_pattern}
							<button class="btn btn-outline btn-square shrink-0" type="button" title={t('shop.set.num.use_default', { pattern: r.default_pattern })} aria-label={t('shop.set.num.use_default', { pattern: r.default_pattern })} onclick={() => useDefault(r)}><RotateCcw size={14} /></button>
						{/if}
					</div>
					<p id="num-{r.kind}-info" class="mt-1 text-xs">
						{#if patErr}
							<span class="text-error" role="alert">{patErr}</span>
						{:else if off}
							<span class="text-muted">{t('shop.set.num.locked')}</span>
						{:else}
							<span class="text-muted">{t('shop.set.num.next')}: <span class="font-mono font-semibold text-ink">{preview?.valid ? preview.number : r.next_number}</span><span class="mx-1.5">·</span>{resetsText(preview?.valid ? preview.resets : r.resets, preview?.valid ? preview.scope_label : r.scope_label)}</span>
						{/if}
					</p>
				</div>
				<div class="min-w-0">
					<label class="mb-1 block text-xs text-muted md:sr-only" for="num-{r.kind}-next">{t('shop.set.num.next_value')}</label>
					<input id="num-{r.kind}-next" class="input input-bordered w-full font-mono" inputmode="numeric" placeholder={String(r.next_sequence)} bind:value={nexts[r.kind]} autocomplete="off" disabled={off} aria-invalid={nextErr ? 'true' : undefined} />
					{#if nextErr}<p class="mt-1 text-xs text-error" role="alert">{nextErr}</p>{/if}
				</div>
			</li>
		{/each}
	</ul>
	<p class="text-xs text-muted">{t('shop.set.num.next_value_help')} {t('shop.set.num.existing')}</p>
</section>
