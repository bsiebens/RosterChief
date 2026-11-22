<script lang="ts">
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTimeLabel, type Bracket, type PublicMatch, type TeamRef } from '#lib/tournaments-public.ts';

	/** A knockout as cards: one column per round, every game a card with both sides, their score and when/where it is played. */
	let { brackets, slug }: { brackets: Bracket[]; slug: string } = $props();

	const played = (m: PublicMatch) => m.status === 'complete' && m.score_home !== null && m.score_away !== null;
	function winner(m: PublicMatch): 'home' | 'away' | null {
		if (!played(m)) return null;
		if (m.score_home !== m.score_away) return m.score_home! > m.score_away! ? 'home' : 'away';
		if (m.penalty_home !== null && m.penalty_away !== null && m.penalty_home !== m.penalty_away) return m.penalty_home > m.penalty_away ? 'home' : 'away';
		return null;
	}
</script>

{#snippet row(team: TeamRef | null, display: string, score: number | null, shots: number | null, win: boolean, lost: boolean)}
	<div class="flex items-center gap-2.5 px-3 py-2 {win ? 'bg-club/10' : ''}">
		{#if team}
			<ClubCrest name={team.name} logoUrl={team.logo_url} size={26} bg={team.primary_color || undefined} />
			<a class="min-w-0 flex-1 truncate text-sm hover:underline {win ? 'font-extrabold text-ink' : lost ? 'text-muted' : 'font-semibold text-ink'}" href="/tournaments/{slug}/teams/{team.slug}">{team.name}</a>
		{:else}
			<span class="flex size-[26px] shrink-0 items-center justify-center rounded-full border border-dashed border-line text-[10px] text-muted">?</span>
			<span class="min-w-0 flex-1 truncate text-sm text-muted italic">{display}</span>
		{/if}
		{#if score !== null}
			<span class="font-mono text-base tabular-nums {win ? 'font-extrabold text-ink' : 'text-muted'}">{score}{#if shots !== null}<sup class="ml-0.5 text-[10px] font-normal">({shots})</sup>{/if}</span>
		{/if}
	</div>
{/snippet}

{#each brackets as b (b.stage.id)}
	{#if b.rounds.length}
		<section class="card overflow-hidden" aria-label={b.stage.name}>
			<div class="border-b border-line px-4 py-3">
				<h3 class="font-display text-base font-bold text-ink">{b.stage.name}</h3>
			</div>
			<div class="overflow-x-auto bg-paper/60 p-4">
				<div class="flex min-w-max gap-5">
					{#each b.rounds as round (round.round_number)}
						<div class="grid w-64 shrink-0 content-start gap-3">
							<h4 class="rounded-full bg-ink px-3 py-1 text-center font-display text-[11px] font-bold tracking-[.12em] text-white uppercase">{round.label}</h4>
							{#each round.matches as m (m.id)}
								{@const w = winner(m)}
								<article class="overflow-hidden rounded-xl border border-line bg-white shadow-sm">
									<div class="flex items-center justify-between gap-2 border-b border-line bg-subhead px-3 py-1 font-mono text-[10px] text-muted">
										<span class="truncate">{m.scheduled_start ? dateTimeLabel(m.scheduled_start) : t('tp.tbd')}</span>
										<span class="truncate">{m.field?.name ?? ''}</span>
									</div>
									{@render row(m.home_team, m.home_display, played(m) ? m.score_home : null, played(m) && m.score_home === m.score_away ? m.penalty_home : null, w === 'home', w === 'away')}
									<div class="border-t border-line"></div>
									{@render row(m.away_team, m.away_display, played(m) ? m.score_away : null, played(m) && m.score_home === m.score_away ? m.penalty_away : null, w === 'away', w === 'home')}
								</article>
							{/each}
						</div>
					{/each}
				</div>
			</div>
		</section>
	{/if}
{/each}
