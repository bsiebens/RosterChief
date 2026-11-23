<script lang="ts">
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { StandingTable } from '#lib/tournaments-public.ts';

	/** One standings table (a group or a whole stage) with the team crests; the first rows are marked as the ones going through. */
	let { title, table, path, through = 0 }: { title: string; table: StandingTable; path: string; through?: number } = $props();
</script>

<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
	<h3 class="border-b border-line bg-subhead px-4 py-2.5 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{title}</h3>
	<div class="overflow-x-auto">
		<table class="w-full text-sm">
			<thead>
				<tr class="font-mono text-[11px] text-muted">
					<th class="w-8 py-2 pl-4 text-left font-normal">#</th>
					<th class="py-2 text-left font-normal">{t('tp.col.team')}</th>
					<th class="px-2 text-right font-normal" title={t('tp.col.played_full')}>{t('tp.col.played')}</th>
					<th class="px-2 text-right font-normal">{t('tp.col.won')}</th>
					<th class="px-2 text-right font-normal">{t('tp.col.drawn')}</th>
					<th class="px-2 text-right font-normal">{t('tp.col.lost')}</th>
					<th class="px-2 text-right font-normal">{t('tp.col.gd')}</th>
					<th class="py-2 pr-4 pl-2 text-right font-normal">{t('tp.col.pts')}</th>
				</tr>
			</thead>
			<tbody>
				{#each table.rows as r, i (r.team.id)}
					<tr class="border-t border-rule hover:bg-paper {i < through ? 'shadow-[inset_3px_0_0_var(--color-club)]' : ''}">
						<td class="py-2 pl-4 font-mono text-xs text-muted">{r.rank}</td>
						<td class="py-2">
							<a class="flex items-center gap-2.5 font-semibold text-ink hover:underline" href="/tournaments/{path}/teams/{r.team.slug}">
								<ClubCrest name={r.team.name} logoUrl={r.team.logo_url} size={24} bg={r.team.primary_color || undefined} /><span class="truncate">{r.team.name}</span>
							</a>
						</td>
						<td class="px-2 text-right font-mono text-xs">{r.played}</td>
						<td class="px-2 text-right font-mono text-xs">{r.won}</td>
						<td class="px-2 text-right font-mono text-xs">{r.drawn}</td>
						<td class="px-2 text-right font-mono text-xs">{r.lost}</td>
						<td class="px-2 text-right font-mono text-xs">{r.goal_diff > 0 ? '+' : ''}{r.goal_diff}</td>
						<td class="py-2 pr-4 pl-2 text-right font-display text-base font-extrabold text-ink">{r.points}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>
