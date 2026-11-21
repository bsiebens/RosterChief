<script lang="ts">
	import MatchList from '#lib/components/tournaments/MatchList.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.schedule.tournament);
	const stages = $derived(data.schedule.stages.filter((s) => s.rounds.length));
</script>

<svelte:head><title>{pageTitle(`${tn.name} · ${t('tp.nav.schedule')}`)}</title></svelte:head>

{#if !stages.length}
	<div class="card card-body items-center py-8 text-center text-muted" role="status">{t('tp.schedule.empty')}</div>
{/if}
{#each stages as s (s.stage.id)}
	<section class="card overflow-hidden">
		<h2 class="px-4 pt-4 pb-1 font-display text-base font-bold text-ink">{s.stage.name}</h2>
		{#each s.rounds as round (round.round_number)}
			<h3 class="px-4 pt-3 font-display text-xs font-bold tracking-[.12em] text-muted uppercase">{round.label}</h3>
			<MatchList matches={round.matches} slug={tn.slug} />
		{/each}
	</section>
{/each}
