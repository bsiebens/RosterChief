<script lang="ts">
	import { ArrowDown, ArrowUp } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { mockAttendance, type Overview } from '#lib/overview.ts';

	/** The six headline numbers, with the change against the previous season where there is one. Attendance is sample data until attendance tracking exists. */
	let { overview }: { overview: Overview } = $props();
	const k = $derived(overview.kpis!);
	const prev = $derived(overview.previous_season?.name ?? '');
	const diff = (now: number, before: number | null) => (before === null ? null : now - before);
	const tiles = $derived([
		{ label: t('overview.kpi.total'), value: String(k.total_members), delta: diff(k.total_members, k.previous_total_members), sub: k.previous_total_members === null ? t('overview.kpi.total_none') : t('overview.kpi.vs', { n: k.previous_total_members, season: prev }), href: '/members?kind=member', warn: false },
		{ label: t('overview.kpi.awaiting'), value: String(k.awaiting_approval), delta: null, sub: t('overview.kpi.awaiting_sub'), href: '/members?kind=member&status=pending', warn: false },
		{ label: t('overview.kpi.no_coach'), value: String(k.teams_without_coach), delta: null, sub: t('overview.kpi.no_coach_sub'), href: '/teams?coach=none', warn: k.teams_without_coach > 0 },
		{ label: t('overview.kpi.unrostered'), value: String(k.unrostered_members), delta: null, sub: t('overview.kpi.unrostered_sub'), href: '/members?kind=member&team=none', warn: false },
		{ label: t('overview.kpi.new'), value: String(k.new_members), delta: diff(k.new_members, k.previous_new_members), sub: k.previous_new_members === null ? t('overview.kpi.new_sub') : t('overview.kpi.vs', { n: k.previous_new_members, season: prev }), href: '/members?kind=member', warn: false },
		{ label: t('overview.kpi.attendance'), value: `${mockAttendance.rate}%`, delta: null, sub: t('overview.kpi.attendance_sub', { n: mockAttendance.sessions }), href: null, warn: false, sample: true }
	]);
</script>

<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
	{#each tiles as tile (tile.label)}
		<svelte:element
			this={tile.href ? 'a' : 'div'}
			href={tile.href ?? undefined}
			class="card card-body gap-1 p-4 {tile.href ? 'transition-colors hover:border-club' : ''} {tile.sample ? 'sample-stripes' : ''}"
			title={tile.sample ? t('overview.sample_hint') : undefined}>
			<span class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{tile.label}</span>
			<span class="flex items-baseline gap-2">
				<span class="font-display text-4xl leading-none font-extrabold {tile.warn ? 'text-club' : 'text-ink'}">{tile.value}</span>
				{#if tile.delta !== null}
					<span class="badge badge-sm gap-0.5 {tile.delta > 0 ? 'badge-success' : tile.delta < 0 ? 'badge-error' : 'badge-ghost'}">
						{#if tile.delta > 0}<ArrowUp size={11} aria-hidden="true" />{:else if tile.delta < 0}<ArrowDown size={11} aria-hidden="true" />{/if}{Math.abs(tile.delta)}
					</span>
				{/if}
			</span>
			<span class="text-sm text-muted">{tile.sub}</span>
		</svelte:element>
	{/each}
</div>
