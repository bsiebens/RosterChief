<script lang="ts">
	import { Shirt } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { analyticsHref } from '#lib/analytics.ts';
	import { seasonLabel, type StaffLine } from '#lib/member-stats.ts';

	/** The teams this person coached or managed, newest first, each with the team's record that season as a win/draw/loss bar. */
	let { staff, canOpenAnalytics = false }: { staff: StaffLine[]; canOpenAnalytics?: boolean } = $props();
	const parts = [
		{ key: 'won', cls: 'bg-ok' },
		{ key: 'drawn', cls: 'bg-dim' },
		{ key: 'lost', cls: 'bg-club' }
	] as const;
</script>

<div class="grid gap-3 md:grid-cols-2">
	{#each staff as line (`${line.team.id}-${line.season?.id ?? ''}`)}
		<div class="card card-body gap-2">
			<div class="flex items-baseline justify-between gap-2">
				<h3 class="flex min-w-0 items-center gap-2 truncate font-display text-base font-bold text-ink"><Shirt size={16} class="shrink-0 text-dim" aria-hidden="true" />
					{#if canOpenAnalytics}<a class="link" href={analyticsHref(line.team.id, line.season?.id)}>{line.team.name}</a>{:else}{line.team.name}{/if}
				</h3>
				<span class="shrink-0 text-xs text-muted">{seasonLabel(line.season)}</span>
			</div>
			<p class="text-sm text-slate">{line.position}</p>
			{#if line.games}
				<div class="flex h-6 overflow-hidden rounded-md bg-rule" role="img" aria-label="{line.won} {t('stats.staff.won')}, {line.drawn} {t('stats.staff.drawn')}, {line.lost} {t('stats.staff.lost')}">
					{#each parts as p (p.key)}
						{#if line[p.key] > 0}<div class="{p.cls} flex items-center justify-center font-mono text-[11px] font-bold text-white" style="width: {(line[p.key] / line.games) * 100}%" title="{line[p.key]} {t(`stats.staff.${p.key}`)}">{line[p.key]}</div>{/if}
					{/each}
				</div>
				<p class="flex justify-between text-xs text-muted"><span>{t('stats.staff.games', { n: line.games })}</span><span>{t('stats.staff.goals', { gf: line.gf, ga: line.ga })}</span></p>
			{:else}
				<p class="text-xs text-muted">{t('stats.staff.none')}</p>
			{/if}
		</div>
	{/each}
</div>
