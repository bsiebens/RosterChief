<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { REF_KINDS, type Summary } from '#lib/referees.ts';

	/** Staffing of the rest of the season per kind (a segmented bar: nobody / partly / fully staffed) and the fees still to fill in. */
	let { summary }: { summary: Summary } = $props();

	const parts = [
		{ key: 'none', fill: 'var(--color-club)' },
		{ key: 'partial', fill: 'var(--color-warn)' },
		{ key: 'full', fill: 'var(--color-ok)' }
	] as const;
	const label = (n: number) => (n === 1 ? t('referees.kpi.games_one') : t('referees.kpi.games', { n }));
</script>

<div class="grid w-full grid-cols-1 gap-3 md:grid-cols-3">
	{#each REF_KINDS as kind (kind)}
		{@const k = summary.kpis[kind]}
		<div class="card card-body gap-2 p-4">
			<div class="flex items-baseline justify-between gap-2">
				<span class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t(`referees.kind.${kind}`)}</span>
				<span class="font-mono text-xs text-muted">{k.games ? label(k.games) : t('referees.kpi.no_games')}</span>
			</div>
			<svg viewBox="0 0 100 6" class="h-2 w-full overflow-hidden rounded-full" role="img" aria-label="{t('referees.kpi.coverage')}: {t(`referees.kind.${kind}`)}" preserveAspectRatio="none">
				<rect width="100" height="6" fill="var(--color-rule)" />
				{#if k.games}
					{#each parts as part, i (part.key)}
						{@const before = parts.slice(0, i).reduce((s, p) => s + k[p.key], 0)}
						{#if k[part.key]}<rect x={(before / k.games) * 100} width={(k[part.key] / k.games) * 100} height="6" fill={part.fill} />{/if}
					{/each}
				{/if}
			</svg>
			<dl class="grid grid-cols-3 gap-2">
				{#each parts as part (part.key)}
					<div>
						<dt class="flex items-center gap-1.5 text-xs text-muted"><span class="h-2 w-2 rounded-full" style="background:{part.fill}"></span>{t(`referees.kpi.${part.key}`)}</dt>
						<dd class="font-display text-2xl leading-none font-extrabold text-ink">{k[part.key]}</dd>
					</div>
				{/each}
			</dl>
		</div>
	{/each}
	<div class="card card-body gap-2 p-4">
		<span class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('referees.kpi.fees')}</span>
		<span class="font-display text-4xl leading-none font-extrabold {summary.fees_pending ? 'text-club' : 'text-ink'}">{summary.fees_pending}</span>
		<p class="text-sm text-muted">{t('referees.kpi.fees_sub')}</p>
	</div>
</div>
