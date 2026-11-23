<script lang="ts">
	import { CalendarClock, Utensils } from '@lucide/svelte';
	import GameCard from '#lib/components/tournaments/public/GameCard.svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dayKey, dayLabel, ties, timeLabel, type PublicBlock, type PublicMatch, type Tie } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const tn = $derived(data.schedule.tournament);
	const all = $derived(ties(data.schedule.stages.flatMap((s) => s.rounds.flatMap((r) => r.matches as PublicMatch[]))));

	let day = $state('');
	let team = $state('');
	let field = $state('');

	const teams = $derived([...new Map(all.flatMap((x) => [x.m.home_team, x.m.away_team]).filter((x) => !!x).map((x) => [x!.id, x!])).values()].sort((a, b) => a.name.localeCompare(b.name, getLang())));
	const fields = $derived([...new Map(all.filter((x) => x.m.field).map((x) => [x.m.field!.id, x.m.field!])).values()].sort((a, b) => a.name.localeCompare(b.name, getLang(), { numeric: true })));
	const days = $derived([...new Set(all.filter((x) => x.start).map((x) => dayKey(x.start!)))].sort());
	const current = $derived(day || days[0] || '');

	type Item = { at: string; tie?: Tie; block?: PublicBlock };
	const items = $derived.by(() => {
		const out: Item[] = [];
		for (const x of all) {
			if (!x.start || dayKey(x.start) !== current) continue;
			if (team && x.m.home_team?.id !== team && x.m.away_team?.id !== team) continue;
			if (field && x.m.field?.id !== field) continue;
			out.push({ at: x.start, tie: x });
		}
		// shared blocks always; a team's own (lunch ...) when that team is picked
		for (const b of data.schedule.blocks) {
			if (dayKey(b.starts_at) !== current) continue;
			if (b.team_ids.length === 0 || (team && b.team_ids.includes(team))) out.push({ at: b.starts_at, block: b });
		}
		return out.sort((a, b) => a.at.localeCompare(b.at));
	});
	// consecutive items starting at the same time share a heading, the games side by side
	const slots = $derived.by(() => {
		const out: { at: string; items: Item[] }[] = [];
		for (const i of items) {
			const key = i.at.slice(0, 16);
			const last = out[out.length - 1];
			if (last && last.at.slice(0, 16) === key) last.items.push(i);
			else out.push({ at: i.at, items: [i] });
		}
		return out;
	});
	const chip = (on: boolean) => `rounded-full border px-4 py-1.5 font-display text-xs font-extrabold tracking-[.08em] uppercase ${on ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}`;
</script>

<svelte:head><title>{pageTitle(`${tn.name} · ${t('tp.nav.schedule')}`)}</title></svelte:head>

{#if !days.length}
	<div class="rounded-2xl border border-line bg-white py-12 text-center text-muted" role="status">{t('tp.schedule.empty')}</div>
{:else}
	<div class="flex flex-wrap items-center gap-3">
		<div class="flex flex-wrap gap-2" role="tablist" aria-label={t('ts.days')}>
			{#each days as d (d)}<button type="button" role="tab" aria-selected={d === current} class={chip(d === current)} onclick={() => (day = d)}>{dayLabel(d, 'short')}</button>{/each}
		</div>
		<div class="ml-auto flex flex-wrap gap-2">
			<select class="select select-sm" bind:value={team} aria-label={t('ts.filter_team')}>
				<option value="">{t('ts.all_teams')}</option>
				{#each teams as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
			</select>
			{#if fields.length > 1}
				<select class="select select-sm" bind:value={field} aria-label={t('ts.filter_field')}>
					<option value="">{t('ts.all_fields')}</option>
					{#each fields as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
				</select>
			{/if}
		</div>
	</div>

	{#if !slots.length}
		<div class="rounded-2xl border border-line bg-white py-10 text-center text-muted" role="status">{t('ts.nothing_here')}</div>
	{/if}
	<!-- one game at a time (a single field): a dense grid, the time is on each card; several fields: a row per start time -->
	{#if slots.every((x) => x.items.length === 1)}
		<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
			{#each items as i, n (i.tie?.id ?? `b${i.block?.id}${n}`)}
				{#if i.tie}
					<GameCard tie={i.tie} path={tn.path} me={teams.find((x) => x.id === team)?.slug ?? ''} />
				{:else if i.block}
					<div class="flex items-center gap-3 rounded-xl border border-dashed border-line bg-white/70 px-4 py-3 sm:col-span-2 xl:col-span-3">
						<span class="flex size-9 shrink-0 items-center justify-center rounded-lg {i.block.kind === 'lunch' ? 'bg-amber-100 text-amber-700' : 'bg-club/10 text-club'}">{#if i.block.kind === 'lunch'}<Utensils size={17} aria-hidden="true" />{:else}<CalendarClock size={17} aria-hidden="true" />{/if}</span>
						<div class="min-w-0">
							<p class="font-semibold text-ink">{i.block.title}</p>
							<p class="font-mono text-xs text-muted">{timeLabel(i.block.starts_at)} – {timeLabel(i.block.ends_at)}{#if i.block.location} · {i.block.location}{/if}</p>
						</div>
					</div>
				{/if}
			{/each}
		</div>
	{:else}
	<ol class="grid gap-4">
		{#each slots as slot (slot.at)}
			<li class="grid gap-3 md:grid-cols-[5.5rem_1fr]">
				<div class="font-display text-2xl leading-none font-extrabold text-ink md:pt-2">{timeLabel(slot.at)}</div>
				<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
					{#each slot.items as i, n (i.tie?.id ?? `b${i.block?.id}${n}`)}
						{#if i.tie}
							<GameCard tie={i.tie} path={tn.path} me={teams.find((x) => x.id === team)?.slug ?? ''} />
						{:else if i.block}
							<div class="flex items-center gap-3 rounded-xl border border-dashed border-line bg-white/70 px-4 py-3 sm:col-span-2 xl:col-span-3">
								<span class="flex size-9 shrink-0 items-center justify-center rounded-lg {i.block.kind === 'lunch' ? 'bg-amber-100 text-amber-700' : 'bg-club/10 text-club'}">{#if i.block.kind === 'lunch'}<Utensils size={17} aria-hidden="true" />{:else}<CalendarClock size={17} aria-hidden="true" />{/if}</span>
								<div class="min-w-0">
									<p class="font-semibold text-ink">{i.block.title}</p>
									<p class="font-mono text-xs text-muted">{timeLabel(i.block.starts_at)} – {timeLabel(i.block.ends_at)}{#if i.block.location} · {i.block.location}{/if}</p>
								</div>
							</div>
						{/if}
					{/each}
				</div>
			</li>
		{/each}
	</ol>
	{/if}
{/if}
