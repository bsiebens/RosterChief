<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { memberHref } from '#lib/attendance.ts';
	import type { Leader } from '#lib/analytics.ts';
	import PlayerFace from '#lib/components/analytics/PlayerFace.svelte';

	/** One leaderboard (top 5): rank, number, name (linked when the game sheet name matched a member) and a bar against the leader. */
	let { title, rows, unit }: { title: string; rows: Leader[]; unit: string } = $props();
</script>

<div class="card card-body gap-3">
	<h2 class="card-title">{title}</h2>
	{#if rows.length}
		<ol class="grid gap-2.5">
			{#each rows as row, i (`${row.member_id ?? row.name}-${i}`)}
				<li class="grid gap-1">
					<div class="flex items-center gap-2 text-sm">
						<span class="w-4 shrink-0 font-mono text-xs text-muted">{i + 1}</span>
						{#if row.number !== null}<span class="shrink-0 font-mono text-xs text-muted">#{row.number}</span>{/if}
						<PlayerFace photoUrl={row.photo_url} initials={row.initials} />
						{#if row.member_id}<a class="link min-w-0 truncate font-semibold" href={memberHref(row.member_id)}>{row.name}</a>{:else}<span class="min-w-0 truncate font-semibold text-ink" title={t('analytics.unmatched')}>{row.name}</span>{/if}
						<span class="ml-auto shrink-0 font-mono font-bold text-ink" aria-label="{row.total} {unit}">{row.total}</span>
					</div>
					<div class="ml-6 h-1.5 overflow-hidden rounded-full bg-rule" aria-hidden="true"><div class="h-full rounded-full {i === 0 ? 'bg-club' : 'bg-ink'}" style="width: {row.pct}%"></div></div>
				</li>
			{/each}
		</ol>
	{:else}
		<p class="py-3 text-center text-sm text-muted">{t('analytics.no_sheet_data')}</p>
	{/if}
</div>
