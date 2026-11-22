<script lang="ts">
	import { Clock, MapPin } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { timeLabel, tieWinner, type Tie, type TeamRef } from '#lib/tournaments-public.ts';

	/**
	 * One game as a card (a back-and-forth tie is one game, its periods split underneath). The whole card opens the game with its timeline.
	 * `me` is a team slug to highlight.
	 */
	let { tie, path, me = '', compact = false }: { tie: Tie; path: string; me?: string; compact?: boolean } = $props();
	const m = $derived(tie.m);
	const winner = $derived(tieWinner(tie));
	const shown = $derived(tie.done || tie.live);
	const periods = $derived(tie.first ? [tie.first, m] : []);
</script>

{#snippet side(team: TeamRef | null, display: string, score: number | null, shots: number | null, win: boolean, mine: boolean, period: (number | null)[])}
	<div class="flex items-center gap-3 px-4 {compact ? 'py-2' : 'py-2.5'} {mine ? 'bg-club/5' : ''}">
		{#if team}
			<ClubCrest name={team.name} logoUrl={team.logo_url} size={compact ? 28 : 34} bg={team.primary_color || undefined} />
			<span class="min-w-0 flex-1 truncate font-display text-[15px] {win ? 'font-extrabold text-ink' : 'font-semibold text-ink/85'} {mine ? 'underline decoration-club decoration-2 underline-offset-4' : ''}">{team.name}</span>
		{:else}
			<span class="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-dashed border-line text-muted">?</span>
			<span class="min-w-0 flex-1 truncate text-sm text-muted italic">{display}</span>
		{/if}
		{#each period as v, i (i)}<span class="w-5 text-right font-mono text-xs text-muted tabular-nums">{shown ? (v ?? '–') : ''}</span>{/each}
		{#if shots !== null && shots !== undefined}<span class="font-mono text-[10px] text-muted">({shots})</span>{/if}
		<span class="w-7 text-right font-display text-xl font-extrabold tabular-nums {win ? 'text-ink' : 'text-ink/70'}">{shown ? (score ?? 0) : ''}</span>
	</div>
{/snippet}

<a href="/tournaments/{path}/games/{m.id}" class="group block overflow-hidden rounded-xl border bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md {tie.live ? 'border-danger/50 ring-1 ring-danger/30' : 'border-line'}">
	<div class="flex items-center gap-x-3 border-b border-line bg-subhead/70 px-4 py-1.5 font-mono text-[11px] text-muted">
		<span class="flex items-center gap-1"><Clock size={11} aria-hidden="true" />{tie.start ? timeLabel(tie.start) : t('tp.tbd')}</span>
		{#if m.field}<span class="flex min-w-0 items-center gap-1 truncate"><MapPin size={11} aria-hidden="true" />{m.field.name}</span>{/if}
		<span class="ml-auto flex items-center gap-2 truncate">
			{#if tie.live}<LiveBadge />{:else}{#if m.group}{m.group.name} · {/if}{m.round_label}{/if}
		</span>
	</div>
	{#if periods.length}
		<div class="flex justify-end gap-3 px-4 pt-1.5 pr-[3.4rem] font-mono text-[10px] text-muted"><span class="w-5 text-right">P1</span><span class="w-5 text-right">P2</span></div>
	{/if}
	{@render side(m.home_team, m.home_display, tie.home, m.penalty_home, winner === 'home', !!me && m.home_team?.slug === me, periods.map((p) => p.score_home))}
	{@render side(m.away_team, m.away_display, tie.away, m.penalty_away, winner === 'away', !!me && m.away_team?.slug === me, periods.map((p) => p.score_away))}
	{#if m.went_to_overtime && tie.done}<p class="px-4 pb-2 font-mono text-[10px] text-muted">{t('tp.overtime')}</p>{/if}
</a>
