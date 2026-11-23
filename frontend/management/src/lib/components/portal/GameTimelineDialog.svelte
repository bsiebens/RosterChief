<script lang="ts">
	import { Goal, ShieldHalf, Timer, X } from '@lucide/svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { GameSheet } from '#lib/portal.ts';

	/**
	 * The game as the competition's page captured it, laid out like the management app's game sheet: the scoreboard on the club's dark card
	 * (crest behind it, final badge, when it was last updated), the score per period, the goal and penalty totals, then every goal, penalty and
	 * goalie change in order by period.
	 */
	let { game, title, crest = null, open = $bindable(false) }: { game: GameSheet; title: string; crest?: string | null; open: boolean } = $props();
	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});
	const icon = (row: { icon: string }) => (row.icon === 'goal' ? Goal : row.icon === 'shield-user' ? ShieldHalf : Timer);
	const rows = $derived(game.periods.flatMap((period) => period.rows));
	const count = (type: string, ours: boolean) => rows.filter((row) => row.type === type && row.ours === ours).length;
	// The scoreboard reads home team first, like a game sheet: when we are the away team our side moves to the right and every "ours–theirs" score flips
	const home = $derived(game.is_home !== false);
	const flip = (value?: string | null) => (!value ? '' : home ? value : value.replace(/^\s*(.*?)\s*([–-])\s*(.*?)\s*$/, '$3$2$1'));
	const [scoreLeft, scoreRight] = $derived(flip(game.score || '–').split(/[–-]/).map((part) => part.trim()));
	const leftLabel = $derived(home ? game.ours_label : game.theirs_label);
	const rightLabel = $derived(home ? game.theirs_label : game.ours_label);
</script>

<dialog bind:this={dialog} class="portal-modal" aria-label={t('portal.timeline_title')} onclose={() => (open = false)} onclick={(e) => { if (e.target === dialog) dialog?.close(); }}>
	{#if open}
		<div class="relative isolate overflow-hidden bg-sidebar-bg text-sidebar-fg">
			{#if crest}<img class="pointer-events-none absolute -top-6 -left-8 -z-10 h-56 w-56 object-contain opacity-15" src={crest} alt="" />{/if}
			<div class="flex items-center gap-3 px-5 pt-3 font-mono text-xs tracking-[.14em] text-sidebar-fg-dim uppercase">
				<span class="min-w-0 flex-1 truncate">{title}</span>
				{#if game.final}<span class="rounded-full border border-sidebar-chip px-2 py-0.5 text-[10px] font-bold text-sidebar-fg">{t('portal.final')}</span>{/if}
				{#if game.updated_at}<span class="hidden normal-case sm:inline">{t('portal.updated', { when: formatDateTime(game.updated_at) })}</span>{/if}
				<button type="button" class="btn btn-square size-9 shrink-0 border-sidebar-fg bg-sidebar-fg! text-sidebar-bg!" aria-label={t('portal.close')} onclick={() => dialog?.close()}><X size={18} strokeWidth={3} aria-hidden="true" /></button>
			</div>
			<div class="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-6">
				<p class="text-right font-display text-xl leading-tight font-extrabold uppercase sm:text-2xl">{leftLabel}</p>
				<p class="flex items-center gap-2 font-display text-5xl leading-none font-extrabold tabular-nums"><span>{scoreLeft}</span><span class="text-sidebar-fg-faint" aria-hidden="true">:</span><span>{scoreRight ?? ''}</span></p>
				<p class="font-display text-xl leading-tight font-extrabold uppercase sm:text-2xl">{rightLabel}</p>
			</div>
			{#if game.period_scores.length}
				<div class="grid border-t border-sidebar-hairline text-center" style="grid-template-columns: repeat({game.period_scores.length}, minmax(0, 1fr))">
					{#each game.period_scores as period, i (i)}
						<div class="py-2 {i ? 'border-l border-sidebar-hairline' : ''}"><p class="font-mono text-[11px] text-sidebar-fg-dim">{period.label}</p><p class="flex items-baseline justify-center gap-2 font-display text-xl font-extrabold">{#each flip(period.score).split(/[–-]/) as part, n (n)}{#if n}<span class="text-sidebar-fg-faint" aria-hidden="true">–</span>{/if}<span>{part.trim()}</span>{/each}</p></div>
					{/each}
				</div>
			{/if}
		</div>
		<div class="flex flex-wrap items-center justify-center gap-x-12 gap-y-1 border-b border-line bg-white px-5 py-3 font-display">
			<span class="inline-flex items-baseline gap-3"><b class="text-xl text-ink">{count('goal', true)}</b><span class="text-xs tracking-[.1em] text-muted uppercase">{t('portal.goals')}</span><b class="text-xl text-muted">{count('goal', false)}</b></span>
			<span class="inline-flex items-baseline gap-3"><b class="text-xl text-ink">{count('penalty', true)}</b><span class="text-xs tracking-[.1em] text-muted uppercase">{t('portal.penalties')}</span><b class="text-xl text-muted">{count('penalty', false)}</b></span>
		</div>
		<div class="grid gap-5 p-5">
			{#each game.periods as period, pi (pi)}
				<section>
					<h3 class="mb-1 flex items-center gap-2 border-b border-line pb-1 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{period.title}{#if period.score}<span class="font-mono text-xs font-normal text-muted">{flip(period.score)}</span>{/if}</h3>
					{#if period.rows.length}
						<ul class="divide-y divide-rule">
							{#each period.rows as row, i (i)}
								{@const Icon = icon(row)}
								<li class="grid grid-cols-[3.25rem_1.5rem_minmax(0,1fr)_auto] items-center gap-3 py-2.5 text-sm {row.serious ? 'bg-danger-bg' : ''}">
									<span class="font-mono text-xs text-muted">{row.time}</span>
									<Icon size={16} class={row.ours === false ? 'text-muted' : 'text-club-dark'} aria-hidden="true" />
									<span class="min-w-0">
										<span class="flex flex-wrap items-center gap-x-2 gap-y-1"><span class="font-semibold text-ink">{row.who}</span>{#if row.tag}<span class="badge badge-xs badge-outline">{row.tag}</span>{/if}</span>
										<span class="mt-0.5 block text-xs text-muted">{row.kind}{row.team ? ` · ${row.team}` : ''}{row.detail ? ` · ${row.detail}` : ''}</span>
									</span>
									<span class="font-display text-lg font-extrabold tabular-nums text-ink">{flip(row.score)}</span>
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
