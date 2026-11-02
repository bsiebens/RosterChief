<script lang="ts">
	import { Plus } from '@lucide/svelte';
	import { NO_SPORT, type Sport } from '#lib/sports.ts';

	let { sports, selected, noSportCount, onadd }: { sports: Sport[]; selected: string; noSportCount: number; onadd: () => void } = $props();

	const row = (active: boolean) => `block w-full border-l-4 px-3 py-2.5 text-left transition-colors hover:bg-bg ${active ? 'border-club bg-bg' : 'border-transparent'}`;
</script>

<nav class="card" aria-label="Sports">
	<div class="flex items-center justify-between gap-2 border-b border-rule px-3 py-2.5">
		<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Sports</h2>
		<button class="btn btn-primary btn-sm" onclick={onadd}><Plus size={14} aria-hidden="true" /> Add sport</button>
	</div>
	<ul class="divide-y divide-rule">
		{#each sports as s (s.id)}
			<li>
				<a href="?sport={s.id}" class={row(selected === s.id)} aria-current={selected === s.id ? 'true' : undefined} data-sveltekit-replacestate data-sveltekit-noscroll>
					<span class="flex items-baseline justify-between gap-2">
						<span class="font-semibold text-ink">{s.name}</span>
						<span class="font-mono text-[11px] text-muted">{s.slug}</span>
					</span>
					<span class="mt-0.5 block text-xs text-muted">{s.club_count} clubs · {s.competition_count} competitions · {s.referee_type_count} referee types</span>
				</a>
			</li>
		{/each}
		<li>
			<a href="?sport={NO_SPORT}" class={row(selected === NO_SPORT)} aria-current={selected === NO_SPORT ? 'true' : undefined} data-sveltekit-replacestate data-sveltekit-noscroll>
				<span class="font-semibold text-ink">No sport</span>
				<span class="mt-0.5 block text-xs text-muted">{noSportCount} competitions without a sport</span>
			</a>
		</li>
	</ul>
	{#if !sports.length}<p class="border-t border-rule px-3 py-3 text-sm text-muted">No sports yet. Add the first one.</p>{/if}
</nav>
