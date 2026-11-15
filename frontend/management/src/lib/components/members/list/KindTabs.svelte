<script lang="ts">
	import { Info } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { KINDS, pageHref, type Kind, type ListFilters } from '#lib/members/list.ts';

	let { filters, onnavigate }: { filters: ListFilters; onnavigate: (href: string) => void } = $props();
	let open = $state(false); // toggled by click/tap
	let hover = $state(false); // pointer over, or keyboard focus on, the button
	const shown = $derived(open || hover);
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (open = false)} onclick={() => (open = false)} />

<div class="flex items-center gap-2">
	<div class="flex items-center gap-1 " role="group" aria-label={t('members.list.kind_label')}>
		{#each KINDS as kind (kind)}
			{@const active = filters.kind === (kind as Kind)}
			<a
				href={pageHref(filters, { kind })}
				aria-current={active ? 'true' : undefined}
				onclick={(e) => {
					e.preventDefault();
					onnavigate(pageHref(filters, { kind }));
				}}
				class="rounded-lg border px-3.5 py-1.5 font-display text-sm font-bold tracking-[.08em] uppercase {active ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}">{t(`members.list.kind.${kind}`)}</a>
		{/each}
	</div>
	<div class="relative" role="presentation" onmouseenter={() => (hover = true)} onmouseleave={() => (hover = false)}>
		<button
			type="button"
			class="btn btn-ghost btn-square"
			aria-label={t('members.list.kind_note_label')}
			aria-expanded={shown}
			onfocus={() => (hover = true)}
			onblur={() => (hover = false)}
			onclick={(e) => {
				e.stopPropagation();
				open = !open;
			}}><Info size={16} /></button>
		{#if shown}
			<div role="note" class="absolute top-full right-0 z-20 mt-2 w-64 rounded-lg border border-line bg-white p-3 font-mono text-xs text-muted shadow-lg">{t('members.list.kind_note')}</div>
		{/if}
	</div>
</div>
