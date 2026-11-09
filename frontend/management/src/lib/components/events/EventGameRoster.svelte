<script lang="ts">
	import { Users } from '@lucide/svelte';
	import PlayerFace from '#lib/components/analytics/PlayerFace.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { memberHref } from '#lib/attendance.ts';
	import type { GameLineup, LineupPlayer } from '#lib/events/types.ts';

	/** Who dressed, from the competition's game page: one team at a time (switch on top), goalies first, then defence, then forwards. */
	let { lineup }: { lineup: GameLineup } = $props();
	const ORDER: Record<string, number> = { GO: 0, DF: 1, FW: 2 };
	const sorted = (list: LineupPlayer[]) => [...list].sort((a, b) => (ORDER[a.pos] ?? 9) - (ORDER[b.pos] ?? 9) || (a.number ?? 999) - (b.number ?? 999));
	let side = $state<'ours' | 'theirs'>('ours');
	const ours = $derived(side === 'ours');
	const list = $derived(sorted(ours ? lineup.ours : lineup.theirs));
	const posLabel = (pos: string) => (pos ? t(`events.roster.pos.${pos}`) : '');
</script>

<section class="card overflow-hidden">
	<header class="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-subhead px-4 py-2.5">
		<h3 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('events.tab.roster')}</h3>
		<div class="flex items-center gap-1 " role="group" aria-label={t('events.tab.roster')}>
			{#each [{ key: 'ours', label: lineup.ours_label }, { key: 'theirs', label: lineup.theirs_label }] as s (s.key)}
				<button type="button" class="max-w-36 truncate rounded-md px-3 py-1 font-display text-xs font-bold tracking-[.06em] uppercase {side === s.key ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}" aria-pressed={side === s.key} onclick={() => (side = s.key as 'ours' | 'theirs')}>{s.label}</button>
			{/each}
		</div>
	</header>
	{#if list.length}
		<table class="table table-fixed">
			<thead>
				<tr>
					<th class="w-12">{t('events.roster.pos')}</th>
					<th class="w-10 text-right">{t('events.roster.nr')}</th>
					<th>{t('events.roster.name')}</th>
					{#if ours}<th class="w-8 text-right" title={t('gamestats.goals')}>{t('gamestats.abbr.g')}</th><th class="w-8 text-right" title={t('gamestats.assists')}>{t('gamestats.abbr.a')}</th><th class="w-10 text-right" title={t('gamestats.pim')}>{t('gamestats.abbr.pim')}</th>{/if}
				</tr>
			</thead>
			<tbody>
				{#each list as p, i (`${p.number}-${p.name}-${i}`)}
					<tr>
						<td><span class="badge badge-sm border {p.pos === 'GO' ? 'border-club bg-club/10 text-club-dark' : 'border-line bg-subhead text-muted'}" title={posLabel(p.pos)}>{p.pos || '–'}</span></td>
						<td class="text-right font-mono text-sm">{p.number ?? '–'}</td>
						<td>
							<span class="flex items-center gap-2">
								<PlayerFace photoUrl={ours ? (p.photo_url ?? null) : null} initials={p.initials} size="size-7" />
								{#if ours && p.member_id}<a class="link min-w-0 truncate font-semibold" href={memberHref(p.member_id)}>{p.name}</a>{:else}<span class="min-w-0 truncate font-semibold text-ink">{p.name}</span>{/if}
							</span>
						</td>
						{#if ours}
							<td class="text-right font-mono text-sm">{p.goals || ''}</td>
							<td class="text-right font-mono text-sm">{p.assists || ''}</td>
							<td class="text-right font-mono text-sm">{p.penalty_minutes || ''}</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="px-4 py-3 text-sm text-muted">{t('events.roster.none')}</p>
	{/if}
</section>
