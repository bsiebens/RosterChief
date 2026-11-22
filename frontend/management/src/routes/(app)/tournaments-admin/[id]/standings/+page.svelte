<script lang="ts">
	import { Medal, Swords } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import BracketView from '#lib/components/tournaments/BracketView.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const s = $derived(data.standings);
	
	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.standings') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.standings'))}</title></svelte:head>

<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
	{#if data.error || !s}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#each s.standings as st (st.stage.id)}
			{#each st.tables as tbl, i (i)}
				<section class="card card-body gap-3">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Medal size={16} class="shrink-0" aria-hidden="true" /> {st.stage.name}{tbl.group ? ` · ${tbl.group.name}` : ''}</h2>
					<div class="overflow-x-auto">
						<table class="table table-sm">
							<thead>
								<tr><th class="w-8">#</th><th>{t('tn.standings.team')}</th><th class="text-right">{t('tn.standings.p')}</th><th class="text-right">{t('tn.standings.w')}</th><th class="text-right">{t('tn.standings.d')}</th><th class="text-right">{t('tn.standings.l')}</th><th class="text-right">{t('tn.standings.gf')}</th><th class="text-right">{t('tn.standings.ga')}</th><th class="text-right">{t('tn.standings.gd')}</th><th class="text-right">{t('tn.standings.pts')}</th>{#each st.attribute_columns ?? [] as c (c.key)}<th class="text-right">{c.name}</th>{/each}</tr>
							</thead>
							<tbody>
								{#each tbl.rows as r (r.team.id)}
									<tr class="hover:bg-paper">
										<td class="font-mono text-xs">{r.rank}</td>
										<td class="font-semibold">{r.team.name}</td>
										<td class="text-right font-mono text-xs">{r.played}</td>
										<td class="text-right font-mono text-xs">{r.won}</td>
										<td class="text-right font-mono text-xs">{r.drawn}</td>
										<td class="text-right font-mono text-xs">{r.lost}</td>
										<td class="text-right font-mono text-xs">{r.goals_for}</td>
										<td class="text-right font-mono text-xs">{r.goals_against}</td>
										<td class="text-right font-mono text-xs">{r.goal_diff > 0 ? '+' : ''}{r.goal_diff}</td>
										<td class="text-right font-mono text-sm font-bold">{r.points}</td>
										{#each st.attribute_columns ?? [] as c (c.key)}<td class="text-right font-mono text-xs">{r.attributes?.[c.key] ?? 0}</td>{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/each}
		{/each}
		{#each s.brackets as b (b.stage.id)}
			<section class="card card-body gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Swords size={16} class="shrink-0" aria-hidden="true" /> {b.stage.name}</h2>
				{#if b.rounds.length}
					<BracketView rounds={b.rounds} />
				{:else}
					<p class="text-sm text-muted">{t('tn.standings.bracket_empty')}</p>
				{/if}
			</section>
		{/each}
		{#if !s.standings.length && !s.brackets.length}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('tn.standings.empty')}</p></div>
		{/if}
	{/if}
</div>
