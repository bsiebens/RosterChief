<script lang="ts">
	import { Goal, ShieldHalf, Timer, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { GameSheet } from '#lib/portal.ts';

	/** The game as the competition's page captured it: the scoreboard, the score by period and every goal, penalty and goalie change in order. */
	let { game, title, open = $bindable(false) }: { game: GameSheet; title: string; open: boolean } = $props();
	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});
	const icon = (row: { icon: string }) => (row.icon === 'goal' ? Goal : row.icon === 'shield-user' ? ShieldHalf : Timer);
</script>

<dialog bind:this={dialog} class="portal-modal" aria-label={t('portal.timeline_title')} onclose={() => (open = false)} onclick={(e) => { if (e.target === dialog) dialog?.close(); }}>
	{#if open}
		<div class="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-(--portal-panel) px-5 py-3">
			<h2 class="min-w-0 flex-1 truncate font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{title}</h2>
			<button type="button" class="btn btn-outline btn-square btn-sm" aria-label={t('portal.close')} onclick={() => dialog?.close()}><X size={16} aria-hidden="true" /></button>
		</div>
		<div class="grid gap-5 p-5">
			<div class="grid grid-cols-[1fr_auto_1fr] items-center gap-4 rounded-box bg-sidebar-bg px-5 py-4 text-white">
				<p class="truncate text-right font-display text-lg font-extrabold uppercase">{game.ours_label}</p>
				<p class="font-display text-4xl leading-none font-extrabold tabular-nums">{game.score || '–'}</p>
				<p class="truncate font-display text-lg font-extrabold text-sidebar-fg-dim uppercase">{game.theirs_label}</p>
			</div>
			{#if game.period_scores.length}
				<div class="flex flex-wrap gap-2">
					{#each game.period_scores as period (period.label)}<span class="badge border border-line"><span class="font-mono text-muted">{period.label}</span>&nbsp;<b class="text-ink">{period.score}</b></span>{/each}
				</div>
			{/if}
			{#each game.periods as period (period.label)}
				<section>
					<h3 class="mb-1 flex items-center gap-2 border-b border-line pb-1 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{period.title}{#if period.score}<span class="font-mono text-xs font-normal text-muted">{period.score}</span>{/if}</h3>
					{#if period.rows.length}
						<ul class="divide-y divide-rule">
							{#each period.rows as row, i (i)}
								{@const Icon = icon(row)}
								<li class="grid grid-cols-[3.25rem_1.5rem_minmax(0,1fr)_auto] items-center gap-2 py-2 text-sm {row.serious ? 'bg-danger-bg' : ''}">
									<span class="font-mono text-xs text-muted">{row.time}</span>
									<Icon size={16} class={row.ours === false ? 'text-muted' : 'text-club-dark'} aria-hidden="true" />
									<span class="min-w-0">
										<span class="block truncate font-semibold text-ink">{row.who}{#if row.tag} <span class="badge badge-xs badge-outline align-middle">{row.tag}</span>{/if}</span>
										<span class="block truncate text-xs text-muted">{row.kind}{row.team ? ` · ${row.team}` : ''}{row.detail ? ` · ${row.detail}` : ''}</span>
									</span>
									<span class="font-display text-lg font-extrabold tabular-nums text-ink">{row.score}</span>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="py-2 text-xs text-muted">–</p>
					{/if}
				</section>
			{/each}
		</div>
	{/if}
</dialog>
