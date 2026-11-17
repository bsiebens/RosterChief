<script lang="ts">
	import { Check, ChevronDown } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';

	/**
	 * The chip in the phone header (person on the member side, team on the coach side): stacked avatars and the current choice, opening a short
	 * list. With `allLabel` the first entry is "everyone" (nothing selected).
	 */
	let { options, value = $bindable(''), allLabel = '' }: { options: { id: string; name: string; short: string; photo?: string | null }[]; value: string; allLabel?: string } = $props();

	let open = $state(false);
	const current = $derived(options.find((o) => o.id === value));
	const stack = $derived(current ? [current] : options.slice(0, 3));
	const label = $derived(current ? current.short : allLabel);
	const pick = (id: string) => {
		value = id;
		open = false;
	};
</script>

<svelte:window onclick={(e) => open && !(e.target as HTMLElement).closest('[data-chip]') && (open = false)} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<div class="relative" data-chip>
	<button type="button" class="phone-chip" aria-haspopup="listbox" aria-expanded={open} onclick={() => (open = !open)}>
		<span class="flex">
			{#each stack as o, i (o.id)}<span class="rounded-full border-2 border-(--portal-panel)" style:margin-left={i ? '-0.5rem' : '0'}><ClubCrest name={o.name} logoUrl={o.photo} size={30} /></span>{/each}
		</span>
		<span class="max-w-28 truncate">{label}</span>
		<ChevronDown size={16} class="shrink-0 opacity-70" aria-hidden="true" />
	</button>
	{#if open}
		<ul class="absolute right-0 z-50 mt-2 grid w-60 gap-0.5 rounded-box border border-line bg-white p-1.5 text-ink shadow-lg" role="listbox">
			{#if allLabel}
				<li><button type="button" class="flex min-h-11 w-full items-center gap-2 rounded px-2 text-left font-semibold hover:bg-subhead" role="option" aria-selected={value === ''} onclick={() => pick('')}>{allLabel}{#if value === ''}<Check size={16} class="ml-auto" aria-hidden="true" />{/if}</button></li>
			{/if}
			{#each options as o (o.id)}
				<li><button type="button" class="flex min-h-11 w-full items-center gap-2 rounded px-2 text-left font-semibold hover:bg-subhead" role="option" aria-selected={value === o.id} onclick={() => pick(o.id)}><ClubCrest name={o.name} logoUrl={o.photo} size={28} />{o.name}{#if value === o.id}<Check size={16} class="ml-auto" aria-hidden="true" />{/if}</button></li>
			{/each}
		</ul>
	{/if}
</div>
