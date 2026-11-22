<script lang="ts">
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTimeLabel, type PublicMatch, type TeamRef } from '#lib/tournaments-public.ts';

	/** A list of matches: when and where, the two sides (linked to their team page) and the result. `highlight` marks one team's own matches. */
	let { matches, slug, highlight = '' }: { matches: PublicMatch[]; slug: string; highlight?: string } = $props();

	const played = (m: PublicMatch) => m.status === 'complete' && m.score_home !== null && m.score_away !== null;
</script>

{#snippet side(team: TeamRef | null, display: string, right: boolean)}
	<span class="flex min-w-0 flex-1 items-center gap-2 {right ? 'flex-row-reverse text-right' : ''}">
		{#if team}
			<ClubCrest name={team.name} logoUrl={team.logo_url} size={24} bg={team.primary_color || undefined} />
			<a class="min-w-0 truncate font-semibold text-ink hover:underline {team.slug === highlight ? 'underline' : ''}" href="/tournaments/{slug}/teams/{team.slug}">{team.name}</a>
		{:else}
			<span class="min-w-0 truncate text-muted italic">{display}</span>
		{/if}
	</span>
{/snippet}

<ul class="divide-y divide-rule">
	{#each matches as m (m.id)}
		<li class="grid gap-1 px-4 py-3">
			<div class="flex flex-wrap items-center gap-x-3 font-mono text-xs text-muted">
				<span>{m.scheduled_start ? dateTimeLabel(m.scheduled_start) : t('tp.tbd')}</span>
				{#if m.field}<span>{m.field.name}</span>{/if}
				{#if m.group}<span>{m.group.name}</span>{/if}
				{#if m.round_label}<span>{m.round_label}</span>{/if}
			</div>
			<div class="flex items-center gap-3">
				{@render side(m.home_team, m.home_display, false)}
				<span class="shrink-0 text-center font-display text-lg font-extrabold text-ink tabular-nums">
					{#if m.status === 'in_progress'}
						<span class="mb-0.5 block"><LiveBadge /></span>{m.score_home ?? 0} – {m.score_away ?? 0}
					{:else if played(m)}
						{m.score_home} – {m.score_away}
						{#if m.went_to_overtime}<span class="block font-mono text-[10px] font-normal text-muted">{t('tp.overtime')}{#if m.penalty_home !== null && m.penalty_away !== null} · {m.penalty_home}–{m.penalty_away} {t('tp.penalties')}{/if}</span>{/if}
					{:else}
						<span class="text-muted">{t('tp.vs')}</span>
					{/if}
				</span>
				{@render side(m.away_team, m.away_display, true)}
			</div>
		</li>
	{/each}
</ul>
