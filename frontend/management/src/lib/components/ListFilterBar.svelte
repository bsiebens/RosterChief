<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import { Search, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * The filter bar above a list: search first (w-56), then the filters (`children`: `select w-auto`, checkboxes), a Clear
	 * button, and on the far right the optional `tabs` (a `FilterTabs`). Search is debounced; `value` is the search currently in
	 * the URL, and the box follows it when the URL changes elsewhere (clear, back button) but never fights the typing.
	 * Full-bleed pages put it directly under the page header, like the members list.
	 */
	let {
		value = '',
		searchLabel,
		searchPlaceholder,
		searchClass = 'w-56',
		onsearch,
		showClear = false,
		onclear,
		children,
		tabs
	}: {
		value?: string;
		searchLabel?: string;
		searchPlaceholder?: string;
		/** Width of the search box: `w-56` fits ~22 characters of placeholder; pass `w-72` for longer ones. */
		searchClass?: string;
		onsearch?: (q: string) => void;
		showClear?: boolean;
		onclear?: () => void;
		children?: Snippet;
		tabs?: Snippet;
	} = $props();

	// svelte-ignore state_referenced_locally
	let q = $state(value);
	$effect(() => {
		const external = value;
		untrack(() => {
			if (external !== q.trim()) q = external;
		});
	});

	let timer: ReturnType<typeof setTimeout> | undefined;
	function onType() {
		clearTimeout(timer);
		timer = setTimeout(() => {
			if (q.trim() !== value) onsearch?.(q.trim());
		}, 400);
	}
</script>

<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
	<div class="flex flex-wrap items-center gap-2" role="search">
		{#if onsearch}
			<div class="relative">
				<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
				<input type="search" aria-label={searchLabel} placeholder={searchPlaceholder} class="input {searchClass} pl-9" bind:value={q} oninput={onType} />
			</div>
		{/if}
		{@render children?.()}
		{#if showClear}
			<button type="button" class="btn btn-outline gap-2" onclick={() => onclear?.()}><X size={14} /> {t('common.clear_filters')}</button>
		{/if}
	</div>
	{#if tabs}
		<div class="flex-1"></div>
		{@render tabs()}
	{/if}
</div>
