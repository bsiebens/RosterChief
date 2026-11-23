<script lang="ts">
	import { SmilePlus } from '@lucide/svelte';
	import { EMOJI, type Reaction } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/** The reaction chips under a bubble (count, highlighted when mine) and the "+" that opens the fixed emoji set. */
	let { reactions, onreact, align = 'start' }: { reactions: Reaction[]; onreact?: (emoji: string) => void; align?: 'start' | 'end' } = $props();
	let open = $state(false);
	let root: HTMLElement | undefined = $state();
</script>

<svelte:window onclick={(e) => { if (open && root && !root.contains(e.target as Node)) open = false; }} onkeydown={(e) => { if (open && e.key === 'Escape') open = false; }} />

{#if reactions.length || onreact}
	<div bind:this={root} class="relative mt-1 flex flex-wrap items-center gap-1 {align === 'end' ? 'justify-end' : ''}">
		{#each reactions as r (r.emoji)}
			<button type="button" class="inline-flex h-6 items-center gap-1 rounded-full border px-2 text-xs {r.mine ? 'border-club bg-club/10 font-bold text-ink' : 'border-line bg-white text-ink'}" aria-pressed={r.mine} aria-label={t('chat.msg.reaction', { emoji: r.emoji, n: r.count })} disabled={!onreact} onclick={() => onreact?.(r.emoji)}>
				<span aria-hidden="true">{r.emoji}</span><span class="font-mono">{r.count}</span>
			</button>
		{/each}
		{#if onreact}
			<button type="button" class="grid size-6 place-items-center rounded-full border border-line bg-white text-muted hover:text-ink" aria-label={t('chat.msg.react')} aria-expanded={open} onclick={() => (open = !open)}><SmilePlus size={13} aria-hidden="true" /></button>
			{#if open}
				<div class="absolute bottom-full z-20 mb-1 flex gap-0.5 rounded-full border border-line bg-white p-1 shadow-lg {align === 'end' ? 'right-0' : 'left-0'}" role="group" aria-label={t('chat.msg.react')}>
					{#each EMOJI as emoji (emoji)}
						<button type="button" class="grid size-9 place-items-center rounded-full text-lg hover:bg-paper" onclick={() => { open = false; onreact(emoji); }}>{emoji}</button>
					{/each}
				</div>
			{/if}
		{/if}
	</div>
{/if}
