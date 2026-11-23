<script lang="ts">
	import { Trophy } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { FinalRow } from '#lib/tournaments-public.ts';

	/** The final ranking: a podium for the first three, then the other teams in columns. `href` makes the teams links to their pages. */
	let { rows, path = '' }: { rows: FinalRow[]; path?: string } = $props();
	const podium = $derived([rows[1], rows[0], rows[2]].filter(Boolean));
	const rest = $derived(rows.slice(3));
	const tone = (rank: number) => (rank === 1 ? 'h-32 bg-gradient-to-b from-amber-300 to-amber-500 text-amber-950' : rank === 2 ? 'h-24 bg-gradient-to-b from-slate-200 to-slate-400 text-slate-800' : 'h-[4.5rem] bg-gradient-to-b from-orange-300 to-orange-600 text-orange-950');
</script>

<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
	<h2 class="flex items-center gap-2 border-b border-line bg-subhead px-5 py-3 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><Trophy size={16} class="text-amber-500" aria-hidden="true" /> {t('ts.final_standings')}</h2>
	<div class="grid gap-6 p-5">
		<div class="mx-auto flex w-full max-w-2xl items-end justify-center gap-2 sm:gap-4">
			{#each podium as r (r.rank)}
				<svelte:element this={path && r.team ? 'a' : 'div'} href={path && r.team ? `/tournaments/${path}/teams/${r.team.slug}` : undefined} class="flex min-w-0 flex-1 flex-col items-center gap-2">
					{#if r.rank === 1}<Trophy size={26} class="text-amber-500" aria-hidden="true" />{/if}
					<ClubCrest name={r.team?.name ?? ''} logoUrl={r.team?.logo_url} bg={r.team?.primary_color || undefined} size={r.rank === 1 ? 72 : 54} />
					<span class="w-full truncate text-center font-display font-extrabold text-ink {r.rank === 1 ? 'text-lg' : 'text-base'}">{r.team?.name}</span>
					<span class="flex w-full items-start justify-center rounded-t-xl pt-2 font-display text-4xl font-extrabold shadow-inner {tone(r.rank)}">{r.rank}</span>
				</svelte:element>
			{/each}
		</div>
		{#if rest.length}
			<ol class="grid gap-x-8 gap-y-1 sm:grid-cols-2 xl:grid-cols-3">
				{#each rest as r (r.rank)}
					<li>
						<svelte:element this={path && r.team ? 'a' : 'div'} href={path && r.team ? `/tournaments/${path}/teams/${r.team.slug}` : undefined} class="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-paper">
							<span class="w-6 text-right font-mono text-sm font-bold text-muted">{r.rank}</span>
							<ClubCrest name={r.team?.name ?? ''} logoUrl={r.team?.logo_url} bg={r.team?.primary_color || undefined} size={30} />
							<span class="min-w-0 truncate font-semibold text-ink">{r.team?.name}</span>
						</svelte:element>
					</li>
				{/each}
			</ol>
		{/if}
	</div>
</section>
