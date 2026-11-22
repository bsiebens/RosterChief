<script lang="ts">
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Bracket, StageStandings } from '#lib/tournaments-public.ts';
	import BracketCards from './BracketCards.svelte';

	/** Standings tables per stage and group, then the knockout brackets round by round. */
	let { standings, brackets, slug, heading = 'h2' }: { standings: StageStandings[]; brackets: Bracket[]; slug: string; heading?: 'h2' | 'h3' } = $props();
	const empty = $derived(!standings.some((s) => s.tables.some((tb) => tb.rows.length)) && !brackets.some((b) => b.rounds.length));
</script>

{#if empty}
	<div class="card card-body items-center py-8 text-center text-muted">{t('tp.standings.empty')}</div>
{/if}

<div class="grid items-start gap-4 lg:grid-cols-2">
{#each standings as s (s.stage.id)}
	{#each s.tables as table (table.group?.id ?? 'all')}
		{#if table.rows.length}
			<section class="card overflow-hidden">
				<div class="px-4 pt-4 pb-2">
					<svelte:element this={heading} class="font-display text-base font-bold text-ink">{s.stage.name}{#if table.group} · {table.group.name}{/if}</svelte:element>
				</div>
				<div class="overflow-x-auto">
					<table class="table">
						<thead>
							<tr>
								<th class="w-8">#</th>
								<th>{t('tp.col.team')}</th>
								<th class="text-right" title={t('tp.col.played_full')}>{t('tp.col.played')}</th>
								<th class="text-right">{t('tp.col.won')}</th>
								<th class="text-right">{t('tp.col.drawn')}</th>
								<th class="text-right">{t('tp.col.lost')}</th>
								<th class="text-right">{t('tp.col.gf')}</th>
								<th class="text-right">{t('tp.col.ga')}</th>
								<th class="text-right">{t('tp.col.gd')}</th>
								<th class="text-right">{t('tp.col.pts')}</th>
							</tr>
						</thead>
						<tbody>
							{#each table.rows as r (r.team.id)}
								<tr>
									<td class="font-mono text-muted">{r.rank}</td>
									<td>
										<a class="flex items-center gap-2 font-semibold hover:underline" href="/tournaments/{slug}/teams/{r.team.slug}">
											<ClubCrest name={r.team.name} logoUrl={r.team.logo_url} size={22} bg={r.team.primary_color || undefined} />
											<span class="min-w-0 truncate">{r.team.name}</span>
										</a>
									</td>
									<td class="text-right tabular-nums">{r.played}</td>
									<td class="text-right tabular-nums">{r.won}</td>
									<td class="text-right tabular-nums">{r.drawn}</td>
									<td class="text-right tabular-nums">{r.lost}</td>
									<td class="text-right tabular-nums">{r.goals_for}</td>
									<td class="text-right tabular-nums">{r.goals_against}</td>
									<td class="text-right tabular-nums">{r.goal_diff > 0 ? '+' : ''}{r.goal_diff}</td>
									<td class="text-right font-bold tabular-nums">{r.points}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	{/each}
{/each}

</div>

<BracketCards {brackets} {slug} />
