<script lang="ts">
	import { Search, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * A dropdown you can search in, for choosing one item out of a list that is already loaded (locations, opponents, ...).
	 * Looks like EntityPicker: the chosen item shows as a chip with a clear button, otherwise a search box with a list that
	 * opens below it (fixed to the viewport so a scrolling card cannot clip it). Filters on the label and the sub text.
	 */
	type Item = { id: string; label: string; sub?: string; badge?: string };
	let {
		items,
		value = $bindable(''),
		id,
		placeholder = '',
		noneLabel = '',
		error,
		label = ''
	}: { items: Item[]; value?: string; id?: string; placeholder?: string; noneLabel?: string; error?: string; label?: string } = $props();

	let q = $state('');
	let focused = $state(false);
	let active = $state(0);
	let field: HTMLInputElement | undefined = $state();
	let pos = $state('');
	const picked = $derived(items.find((i) => i.id === value) ?? null);
	const fold = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
	const words = $derived(fold(q).split(/\s+/).filter(Boolean));
	const hits = $derived(items.filter((i) => words.every((w) => fold(`${i.label} ${i.sub ?? ''}`).includes(w))));
	// With a "none" choice (clear) as the first entry only when the search is empty.
	const MAX_H = 260;

	function place() {
		if (!field) return;
		const r = field.getBoundingClientRect();
		const below = window.innerHeight - r.bottom - 12;
		const above = r.top - 12;
		const up = below < 160 && above > below;
		const h = Math.min(MAX_H, up ? above : below);
		pos = `left:${r.left}px;width:${r.width}px;max-height:${h}px;` + (up ? `bottom:${window.innerHeight - r.top + 4}px;` : `top:${r.bottom + 4}px;`);
	}
	$effect(() => {
		if (!focused) return;
		place();
		const on = () => place();
		window.addEventListener('resize', on);
		window.addEventListener('scroll', on, true);
		return () => {
			window.removeEventListener('resize', on);
			window.removeEventListener('scroll', on, true);
		};
	});
	$effect(() => {
		hits.length;
		active = 0;
	});
	function choose(i: Item) {
		value = i.id;
		q = '';
		focused = false;
	}
	function clear() {
		value = '';
		q = '';
	}
	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			active = Math.min(active + 1, hits.length - 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			active = Math.max(active - 1, 0);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (hits[active]) choose(hits[active]);
		} else if (e.key === 'Escape') focused = false;
	}
</script>

<div class="w-full min-w-0">
	{#if picked}
		<div class="flex min-h-9 items-center justify-between gap-2 rounded-lg border px-3 py-1.5 {error ? 'border-danger' : 'border-line'}">
			<span class="min-w-0 truncate"><span class="font-semibold text-ink">{picked.label}</span>{#if picked.sub}<span class="ml-2 text-xs text-muted">{picked.sub}</span>{/if}{#if picked.badge}<span class="badge badge-sm badge-success ml-2">{picked.badge}</span>{/if}</span>
			<button class="btn btn-ghost btn-xs btn-square shrink-0" type="button" aria-label={noneLabel || t('common.clear')} onclick={clear}><X size={14} /></button>
		</div>
	{:else}
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input
				{id}
				bind:this={field}
				type="search"
				role="combobox"
				aria-expanded={focused}
				aria-controls={id ? `${id}-list` : undefined}
				aria-label={label || placeholder}
				class="input input-bordered w-full pl-9 {error ? 'input-error' : ''}"
				{placeholder}
				autocomplete="off"
				bind:value={q}
				onfocus={() => {
					focused = true;
					place();
				}}
				onblur={() => setTimeout(() => (focused = false), 150)}
				{onkeydown} />
		</div>
		{#if focused}
			<div id={id ? `${id}-list` : undefined} role="listbox" class="fixed z-50 overflow-auto rounded-lg border border-line bg-white shadow-lg" style={pos}>
				{#if noneLabel && !q.trim()}
					<button class="flex w-full items-center px-3 py-2 text-left text-sm text-muted hover:bg-surface focus:bg-surface focus:outline-none" type="button" onmousedown={(e) => e.preventDefault()} onclick={() => ((value = ''), (focused = false))}>{noneLabel}</button>
				{/if}
				{#if hits.length}
					<ul class="divide-y divide-rule">
						{#each hits as h, i (h.id)}
							<li role="option" aria-selected={i === active}>
								<button class="flex w-full flex-col items-start px-3 py-2 text-left hover:bg-surface focus:bg-surface focus:outline-none {i === active ? 'bg-surface' : ''}" type="button" onmousedown={(e) => e.preventDefault()} onclick={() => choose(h)}>
									<span class="text-sm font-semibold text-ink">{h.label}{#if h.badge}<span class="badge badge-sm badge-success ml-2">{h.badge}</span>{/if}</span>
									{#if h.sub}<span class="text-xs text-muted">{h.sub}</span>{/if}
								</button>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="px-3 py-2 text-sm text-muted">{t('families.picker.none')}</p>
				{/if}
			</div>
		{/if}
	{/if}
</div>
