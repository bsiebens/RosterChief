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
	const CAP = 18; // caption row of a box: its round, and the leg columns
	const ROW = $derived(twoLegged ? 34 : 28);
	const H = $derived(CAP + 2 * ROW);
	const STEP = $derived(H + SPACE);

	type Node = { m: MatchDoc; col: number; y: number; lane: number };
	const LANE_HEAD = 26; // label of a lower lane
	const LANE_GAP = 28;
	/**
	 * Games are stacked in lanes: the main bracket on top, and every step down into the "loser of" games one lane lower, so a placement
	 * ladder runs underneath the final instead of crossing it. Within a lane a game sits between the games that feed it.
	 */
	const layout = $derived.by(() => {
		const raw: { m: MatchDoc; round: number }[] = [];
		rounds.forEach((r, round) => (r.matches ?? []).filter((m) => m.leg !== 1).forEach((m) => raw.push({ m, round })));
		const lane = new Map<string, number>();
		for (const { m } of raw) {
			let l = 0;
			for (const s of [m.home_source, m.away_source]) {
				if (s?.type === 'match' && lane.has(s.match_id)) l = Math.max(l, lane.get(s.match_id)! + (s.is_winner ? 0 : 1));
			}
			lane.set(m.id, l);
		}
		// Columns by depth: the main bracket runs round by round; a placement game stands one column after the latest game it is fed by,
		// so "Semifinal" and "Ranks 5-8 - Semifinal" (and the final, 3rd and 5th place games) line up.
		const mainRounds = [...new Set(raw.filter((r) => lane.get(r.m.id) === 0).map((r) => r.round))].sort((a, b) => a - b);
		const colOf = new Map<string, number>();
		for (const { m, round } of raw) {
			if (lane.get(m.id) === 0) {
				colOf.set(m.id, mainRounds.indexOf(round));
				continue;
			}
			const fed = [m.home_source, m.away_source].flatMap((s) => (s?.type === 'match' && colOf.has(s.match_id) ? [colOf.get(s.match_id)!] : []));
			colOf.set(m.id, fed.length ? Math.max(...fed) + 1 : 0);
		}
		const items = raw.map(({ m }) => ({ m, col: colOf.get(m.id)! }));
		const columns = Math.max(0, ...items.map((i) => i.col)) + 1;
		const headings = Array.from({ length: columns }, (_, c) => {
			const here = items.filter((i) => i.col === c);
			return (here.find((i) => lane.get(i.m.id) === 0) ?? here[0])?.m.round_label || '';
		});
		const lanes = Math.max(0, ...lane.values()) + 1;
		const nodes = new Map<string, Node>();
		let top = 0;
		for (let l = 0; l < lanes; l++) {
			const offset = top + (l > 0 ? LANE_HEAD : 0);
			let bottom = 0;
			for (let col = 0; col < columns; col++) {
				const mine = items.filter((i) => i.col === col && lane.get(i.m.id) === l);
				const wanted = mine.map(({ m }) => {
					const ys = [m.home_source, m.away_source].flatMap((s) => (s?.type === 'match' && nodes.has(s.match_id) && nodes.get(s.match_id)!.lane === l ? [nodes.get(s.match_id)!.y - offset] : []));
					return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : null;
				});
				let cursor = -STEP;
				let order = mine.map((o, i) => ({ m: o.m, y: wanted[i] as number | null, i }));
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
					nodes.set(o.m.id, { m: o.m, col, y: offset + y, lane: l });
					bottom = Math.max(bottom, offset + y + H);
				}
			}
			top = bottom + LANE_GAP;
		}
		return { nodes, lanes, columns, headings, laneTops: Array.from({ length: lanes }, (_, l) => Math.min(Infinity, ...[...nodes.values()].filter((n) => n.lane === l).map((n) => n.y)) - (l > 0 ? LANE_HEAD : 0)) };
	});
	const nodes = $derived(layout.nodes);
	const height = $derived(Math.max(0, ...[...nodes.values()].map((n) => n.y + H)));
	const width = $derived(layout.columns * (W + GAP) - GAP);

	let hover = $state<string | null>(null);
	/** What feeds a game (every earlier game its teams come from, winners and losers alike) and what it feeds. */
	const parents = $derived.by(() => {
		const out = new Map<string, string[]>();
		for (const n of nodes.values()) out.set(n.m.id, [n.m.home_source, n.m.away_source].flatMap((s) => (s?.type === 'match' && nodes.has(s.match_id) ? [s.match_id] : [])));
		return out;
	});
	const children = $derived.by(() => {
		const out = new Map<string, string[]>();
		for (const [id, ps] of parents) for (const p of ps) out.set(p, [...(out.get(p) ?? []), id]);
		return out;
	});
	/** Hovering a game: the full chain of games before it, and the games that follow it directly. */
	const focus = $derived.by(() => {
		if (!hover) return null;
		const before = new Set<string>();
		const queue = [...(parents.get(hover) ?? [])];
		while (queue.length) {
			const id = queue.pop()!;
			if (before.has(id)) continue;
			before.add(id);
			queue.push(...(parents.get(id) ?? []));
		}
		return { before, next: new Set(children.get(hover) ?? []) };
	});
	const role = (id: string) => (!hover ? '' : id === hover ? 'self' : focus?.before.has(id) ? 'before' : focus?.next.has(id) ? 'next' : 'other');
	/** Without a hover only the winner lines of a lane are drawn; hovering shows the chain before the game and the lines to what follows. */
	function linkOpacity(l: { from: string; to: string; loser: boolean; sameLane: boolean }) {
		if (!hover) return l.loser || !l.sameLane ? 0 : 0.7;
		const inChain = (l.to === hover || focus?.before.has(l.to)) && (focus?.before.has(l.from) ?? false);
		return inChain || l.from === hover ? 1 : 0.06;
	}
	type Link = { d: string; loser: boolean; from: string; to: string; sameLane: boolean };
	const links = $derived.by(() => {
		type Raw = { from: Node; n: Node; side: 'home_source' | 'away_source'; winner: boolean; fromId: string };
		const raw: Raw[] = [];
		for (const n of nodes.values()) {
			for (const side of ['home_source', 'away_source'] as const) {
				const s = n.m[side];
				const from = s?.type === 'match' ? nodes.get(s.match_id) : undefined;
				if (s && s.type === 'match' && from) raw.push({ from, n, side, winner: s.is_winner, fromId: s.match_id });
			}
		}
		// Every "loser goes to" line gets a vertical track of its own, left of where the winner lines bend (the middle of the gap), so none of
		// them shares a stretch with another line and each keeps a straight run into its arrowhead.
		const losers = raw.filter((r) => !r.winner).sort((a, b) => a.from.col - b.from.col || a.from.y - b.from.y || a.n.y - b.n.y);
		const perColumn = new Map<number, number>();
		for (const r of losers) perColumn.set(r.from.col, (perColumn.get(r.from.col) ?? 0) + 1);
		const seen = new Map<number, number>();
		const track = new Map<Raw, number>();
		for (const r of losers) {
			const i = seen.get(r.from.col) ?? 0;
			seen.set(r.from.col, i + 1);
			track.set(r, 6 + i * Math.min(5, (GAP / 2 - 12) / perColumn.get(r.from.col)!));
		}
		return raw.map((r): Link => {
			const x1 = r.from.col * (W + GAP) + W;
			const x2 = r.n.col * (W + GAP);
			const y1 = r.from.y + H / 2;
			const y2 = r.n.y + CAP + ROW / 2 + (r.side === 'home_source' ? 0 : ROW);
			const bend = r.winner ? x1 + GAP / 2 : x1 + track.get(r)!;
			return { d: `M${x1} ${y1} H${bend} V${y2} H${x2 - 1}`, loser: !r.winner, from: r.fromId, to: r.n.m.id, sameLane: r.from.lane === r.n.lane };
		});
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
	<p class="mb-1 flex flex-wrap items-center gap-4 text-xs text-muted">
		<span>{t('tn.bracket.hover_hint')}</span>
		<span class="flex items-center gap-1.5"><span class="inline-block size-3 rounded border border-sky-500 bg-sky-50"></span>{t('tn.bracket.before')}</span>
		<span class="flex items-center gap-1.5"><span class="inline-block size-3 rounded border border-emerald-500 bg-emerald-50"></span>{t('tn.bracket.next')}</span>
	</p>
	<div class="overflow-x-auto overflow-y-hidden rounded-lg border border-line bg-paper/40 p-3">
		<div class="relative" style:width="{width}px" style:height="{height + HEAD}px">
			{#each layout.headings as label, col (col)}
				<h3 class="absolute truncate text-xs font-bold text-muted" style:left="{col * (W + GAP)}px" style:width="{W}px" style:top="0">{label || t('tn.standings.round', { n: col + 1 })}</h3>
			{/each}
			{#each layout.laneTops as top, l (l)}
				{#if l > 0}
					<div class="absolute right-0 left-0 border-t border-dashed border-line" style:top="{HEAD + top - LANE_GAP / 2}px"></div>
					<p class="absolute text-[11px] font-bold tracking-wide text-muted uppercase" style:left="0" style:top="{HEAD + top + 2}px">{t('tn.bracket.placement')}</p>
				{/if}
			{/each}
			<svg class="pointer-events-none absolute left-0" style:top="{HEAD}px" {width} {height} aria-hidden="true">
				<defs>
					<marker id="bk-win" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="currentColor" class="text-ink/60" /></marker>
					<marker id="bk-lose" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="currentColor" class="text-amber-600" /></marker>
				</defs>
				{#each links as l, i (i)}
					<path
						d={l.d}
						fill="none"
						stroke="currentColor"
						class="{l.loser ? 'text-amber-600' : 'text-ink/55'} transition-opacity"
						stroke-width={hover && linkOpacity(l) === 1 ? 2.25 : 1.5}
						opacity={linkOpacity(l)}
						marker-end="url(#{l.loser ? 'bk-lose' : 'bk-win'})" />
				{/each}
			</svg>
			{#each nodes.values() as n (n.m.id)}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div class="absolute overflow-hidden rounded-lg border text-xs shadow-sm transition-opacity {role(n.m.id) === 'self' ? 'border-ink bg-white ring-2 ring-ink' : role(n.m.id) === 'before' ? 'border-sky-500 bg-sky-50 ring-1 ring-sky-400' : role(n.m.id) === 'next' ? 'border-emerald-500 bg-emerald-50 ring-1 ring-emerald-400' : 'border-line bg-white'} {role(n.m.id) === 'other' ? 'opacity-35' : ''}" onmouseenter={() => (hover = n.m.id)} onmouseleave={() => (hover = null)} style:left="{n.col * (W + GAP)}px" style:top="{HEAD + n.y}px" style:width="{W}px" style:height="{H}px">
					<div class="flex h-[18px] items-center justify-between gap-2 border-b border-line bg-subhead px-2 font-mono text-[10px] text-muted">
						<span class="truncate">{n.m.round_label}</span>
						{#if n.m.leg}<span class="flex shrink-0 items-center gap-2"><span>{t('tn.leg', { n: 1 })}</span><span>{t('tn.leg', { n: 2 })}</span><span class="w-5 text-right">Σ</span></span>{/if}
					</div>
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
