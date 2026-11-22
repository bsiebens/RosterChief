<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { MatchDoc } from '#lib/tournaments.svelte.ts';

	/**
	 * A knockout drawn as a bracket: one column per round, each game centred between the games that feed it, with elbow lines from a game
	 * to the one its winner (solid) or loser (dashed) goes to. A back-and-forth tie is one box with both legs and the total.
	 */
	let { rounds }: { rounds: { round_number?: number; label?: string; matches?: MatchDoc[] }[] } = $props();

	const W = 300; // box width
	const GAP = 56; // room for the connectors between columns
	const HEAD = 30; // column headings
	const SPACE = 12; // between boxes of a column

	const all = $derived(new Map(rounds.flatMap((r) => r.matches ?? []).map((m) => [m.id, m])));
	const twoLegged = $derived([...all.values()].some((m) => m.leg));
	const H = $derived(twoLegged ? 92 : 58);
	const STEP = $derived(H + SPACE);

	type Node = { m: MatchDoc; col: number; y: number };
	const layout = $derived.by(() => {
		const nodes = new Map<string, Node>();
		rounds.forEach((r, col) => {
			const items = (r.matches ?? []).filter((m) => m.leg !== 1); // a first leg lives inside its tie's box
			const wanted = items.map((m) => {
				const ys = [m.home_source, m.away_source].flatMap((s) => (s?.type === 'match' && nodes.has(s.match_id) ? [nodes.get(s.match_id)!.y] : []));
				return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : null;
			});
			let order = items.map((m, i) => ({ m, y: wanted[i] as number | null, i }));
			// games without a source column go below the previous one; the rest sit between their feeders
			let cursor = -STEP;
			order = order.map((o) => {
				if (o.y === null) {
					cursor += STEP;
					return { ...o, y: cursor };
				}
				cursor = Math.max(cursor, o.y);
				return o;
			});
			order.sort((a, b) => (a.y as number) - (b.y as number) || a.i - b.i);
			let last = -Infinity;
			for (const o of order) {
				const y = Math.max(o.y as number, last + STEP);
				last = y;
				nodes.set(o.m.id, { m: o.m, col, y });
			}
		});
		return nodes;
	});
	const height = $derived(Math.max(0, ...[...layout.values()].map((n) => n.y + H)));
	const width = $derived(rounds.length * (W + GAP) - GAP);

	type Link = { d: string; loser: boolean };
	const links = $derived.by(() => {
		const out: Link[] = [];
		for (const n of layout.values()) {
			for (const side of ['home_source', 'away_source'] as const) {
				const s = n.m[side];
				const from = s?.type === 'match' ? layout.get(s.match_id) : undefined;
				if (!s || s.type !== 'match' || !from) continue;
				const x1 = from.col * (W + GAP) + W;
				const x2 = n.col * (W + GAP);
				const y1 = from.y + H / 2;
				const y2 = n.y + H / 2 + (side === 'home_source' ? -6 : 6);
				const mid = x1 + (x2 - x1) / 2;
				out.push({ d: `M${x1} ${y1} H${mid} V${y2} H${x2}`, loser: !s.is_winner });
			}
		}
		return out;
	});

	const score = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v));
	/** The lines of a box: per team its legs and total (two-legged) or its score. */
	function rows(m: MatchDoc) {
		const first = m.first_leg_id ? all.get(m.first_leg_id) : null;
		const done = m.score_home !== null && m.score_away !== null;
		const totalHome = first ? (first.score_home ?? 0) + (m.score_home ?? 0) : m.score_home;
		const totalAway = first ? (first.score_away ?? 0) + (m.score_away ?? 0) : m.score_away;
		const homeWins = done && (totalHome! > totalAway! || (totalHome === totalAway && (m.penalty_home ?? 0) > (m.penalty_away ?? 0)));
		const awayWins = done && (totalAway! > totalHome! || (totalHome === totalAway && (m.penalty_away ?? 0) > (m.penalty_home ?? 0)));
		return [
			{ name: m.home_display, legs: first ? [score(first.score_home), score(m.score_home)] : [], total: done ? score(totalHome) : '', win: homeWins, shots: m.penalty_home },
			{ name: m.away_display, legs: first ? [score(first.score_away), score(m.score_away)] : [], total: done ? score(totalAway) : '', win: awayWins, shots: m.penalty_away }
		];
	}
</script>

{#if rounds.length}
	<div class="max-h-[75vh] overflow-auto rounded-lg border border-line bg-paper/40 p-3">
		<div class="relative" style:width="{width}px" style:height="{height + HEAD}px">
			{#each rounds as r, col (col)}
				<h3 class="absolute truncate text-xs font-bold text-muted" style:left="{col * (W + GAP)}px" style:width="{W}px" style:top="0">{r.label ?? t('tn.standings.round', { n: r.round_number ?? col + 1 })}</h3>
			{/each}
			<svg class="pointer-events-none absolute left-0" style:top="{HEAD}px" {width} {height} aria-hidden="true">
				{#each links as l, i (i)}
					<path d={l.d} fill="none" stroke="currentColor" class="text-ink/35" stroke-width="1.5" stroke-dasharray={l.loser ? '4 3' : undefined} />
				{/each}
			</svg>
			{#each layout.values() as n (n.m.id)}
				<div class="absolute overflow-hidden rounded-lg border border-line bg-white text-xs shadow-sm" style:left="{n.col * (W + GAP)}px" style:top="{HEAD + n.y}px" style:width="{W}px" style:height="{H}px">
					{#if n.m.leg}
						<div class="flex items-center justify-end gap-2 border-b border-line bg-subhead px-2 py-0.5 font-mono text-[10px] text-muted"><span>{t('tn.leg', { n: 1 })}</span><span>{t('tn.leg', { n: 2 })}</span><span class="w-5 text-right">Σ</span></div>
					{/if}
					{#each rows(n.m) as row, i (i)}
						<div class="flex items-center justify-between gap-2 px-2 {i === 0 ? 'border-b border-line' : ''} {n.m.leg ? 'h-[34px]' : 'h-[28px]'}">
							<span class="truncate {row.win ? 'font-bold text-ink' : 'text-ink/80'}">{row.name}</span>
							<span class="flex shrink-0 items-center gap-2 font-mono">
								{#each row.legs as v, k (k)}<span class="w-6 text-right text-muted">{v}</span>{/each}
								{#if row.shots !== null && row.shots !== undefined}<span class="text-[10px] text-muted">({row.shots})</span>{/if}
								<span class="w-5 text-right font-bold {row.win ? 'text-ink' : 'text-ink/70'}">{row.total}</span>
							</span>
						</div>
					{/each}
				</div>
			{/each}
		</div>
	</div>
{/if}
