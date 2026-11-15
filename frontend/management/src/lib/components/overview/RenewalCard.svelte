<script lang="ts">
	import { RefreshCw, ArrowDown, ArrowUp } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Overview } from '#lib/overview.ts';

	let { renewal }: { renewal: Overview['renewal'] } = $props();
	const diff = $derived(renewal && renewal.previous_rate !== null ? renewal.rate - renewal.previous_rate : null);
</script>

<div class="card card-body">
	<h2 class="card-title flex items-center gap-2"><RefreshCw size={16} class="shrink-0" aria-hidden="true" /> {t('overview.renewal.title')}</h2>
	{#if !renewal}
		<p class="text-sm text-muted">{t('overview.renewal.none')}</p>
	{:else}
		<p class="flex items-baseline gap-3 font-display text-5xl font-extrabold text-ink">
			{renewal.rate}%
			{#if diff !== null && diff !== 0}<span class="badge gap-0.5 {diff > 0 ? 'badge-success' : 'badge-error'}">{#if diff > 0}<ArrowUp size={11} aria-hidden="true" />{:else}<ArrowDown size={11} aria-hidden="true" />{/if}{Math.abs(diff)}</span>{/if}
		</p>
		<div class="flex h-2.5 overflow-hidden rounded-full bg-rule" role="img" aria-label="{renewal.renewed} / {renewal.of}">
			<div class="h-full bg-club" style="width: {renewal.rate}%" title="{t('overview.renewal.came_back')}: {renewal.renewed}"></div>
		</div>
		<div class="flex justify-between text-xs">
			<span class="inline-flex items-center gap-1.5 text-muted"><span class="h-2.5 w-2.5 rounded-sm bg-club"></span>{t('overview.renewal.came_back')} <span class="font-mono font-semibold text-ink">{renewal.renewed}</span></span>
			<span class="inline-flex items-center gap-1.5 text-muted"><span class="h-2.5 w-2.5 rounded-sm bg-rule"></span>{t('overview.renewal.left')} <span class="font-mono font-semibold text-ink">{renewal.of - renewal.renewed}</span></span>
		</div>
		{#if diff !== null && renewal.previous_rate !== null}
			<p class="text-xs text-muted">{diff === 0 ? t('overview.renewal.same', { rate: renewal.previous_rate }) : t('overview.renewal.vs', { n: Math.abs(diff), dir: t(diff > 0 ? 'overview.renewal.higher' : 'overview.renewal.lower'), rate: renewal.previous_rate })}</p>
		{/if}
		<p class="text-sm text-muted">{t('overview.renewal.detail', { renewed: renewal.renewed, of: renewal.of, season: renewal.previous_season })}</p>
	{/if}
</div>
