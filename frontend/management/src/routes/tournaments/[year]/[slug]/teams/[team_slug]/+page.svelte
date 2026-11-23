<script lang="ts">
	import { CalendarClock, ChevronLeft, DoorOpen, Shirt, Users, Utensils } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import GameCard from '#lib/components/tournaments/public/GameCard.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dayKey, dayLabel, ties, timeLabel, type PublicBlock, type PublicMatch, type Tie } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const d = $derived(data.team);
	const path = $derived(d.tournament.path);
	const players = $derived(d.roster.filter((r) => r.role === 'player'));
	const staff = $derived(d.roster.filter((r) => r.role === 'staff'));

	type Item = { at: string; tie?: Tie; block?: PublicBlock };
	// the team's programme: its games and its own blocks (lunch ...) in the order they happen, per day
	const program = $derived.by(() => {
		const items: Item[] = [
			...ties(d.matches as PublicMatch[]).filter((x) => x.start).map((x) => ({ at: x.start!, tie: x })),
			...d.blocks.map((b) => ({ at: b.starts_at, block: b }))
		].sort((a, b) => a.at.localeCompare(b.at));
		const days: { day: string; items: Item[] }[] = [];
		for (const i of items) {
			const k = dayKey(i.at);
			const last = days[days.length - 1];
			if (last && last.day === k) last.items.push(i);
			else days.push({ day: k, items: [i] });
		}
		return days;
	});
	const unscheduled = $derived(ties(d.matches as PublicMatch[]).filter((x) => !x.start));
</script>

<svelte:head><title>{pageTitle(`${d.team.name} · ${d.tournament.name}`)}</title></svelte:head>

<a class="link inline-flex items-center gap-1 text-sm" href="/tournaments/{path}/teams"><ChevronLeft size={14} aria-hidden="true" /> {t('tp.nav.teams')}</a>

<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
	<div class="h-3" style:background={d.team.primary_color || 'var(--color-club)'}></div>
	<div class="flex flex-wrap items-center gap-5 p-5 sm:p-6">
		<ClubCrest name={d.team.name} logoUrl={d.team.logo_url} size={92} bg={d.team.primary_color || undefined} />
		<div class="min-w-0">
			<h1 class="font-display text-3xl leading-tight font-extrabold text-ink uppercase sm:text-4xl">{d.team.name}</h1>
			{#if d.team.withdrawn}<p class="mt-1 text-sm text-danger">{t('tp.team.withdrawn')}</p>{/if}
			<p class="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-muted">
				<span class="flex items-center gap-1.5"><Users size={13} aria-hidden="true" />{t('ts.players_n', { n: players.length })}</span>
				{#each d.dressing_rooms as r (r.name)}<span class="flex items-center gap-1.5"><DoorOpen size={13} aria-hidden="true" />{r.name}</span>{/each}
			</p>
		</div>
	</div>
</section>

<div class="grid items-start gap-5 lg:grid-cols-3">
	<div class="grid min-w-0 content-start gap-5 lg:col-span-2">
		<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{t('ts.program')}</h2>
		{#if !program.length && !unscheduled.length}
			<div class="rounded-2xl border border-line bg-white py-10 text-center text-muted" role="status">{t('tp.team.no_matches')}</div>
		{/if}
		{#each program as g (g.day)}
			<section class="grid gap-3">
				<h3 class="font-display text-xs font-extrabold tracking-[.14em] text-club uppercase">{dayLabel(g.day, 'short')}</h3>
				<ol class="relative grid gap-3 border-l-2 border-line pl-5">
					{#each g.items as i, n (i.tie?.id ?? `b${i.block?.id}${n}`)}
						<li class="relative">
							<span class="absolute top-1/2 left-[calc(-1.25rem-1px)] size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white {i.block ? (i.block.kind === 'lunch' ? 'bg-amber-500' : 'bg-club') : 'bg-ink'}"></span>
							{#if i.tie}
								<GameCard tie={i.tie} {path} me={d.team.slug} />
							{:else if i.block}
								<div class="flex items-center gap-3 rounded-xl border border-dashed border-line bg-white px-4 py-3">
									<span class="flex size-9 shrink-0 items-center justify-center rounded-lg {i.block.kind === 'lunch' ? 'bg-amber-100 text-amber-700' : 'bg-club/10 text-club'}">{#if i.block.kind === 'lunch'}<Utensils size={17} aria-hidden="true" />{:else}<CalendarClock size={17} aria-hidden="true" />{/if}</span>
									<div class="min-w-0">
										<p class="font-semibold text-ink">{i.block.title}</p>
										<p class="font-mono text-xs text-muted">{timeLabel(i.block.starts_at)} – {timeLabel(i.block.ends_at)}{#if i.block.location} · {i.block.location}{/if}</p>
									</div>
								</div>
							{/if}
						</li>
					{/each}
				</ol>
			</section>
		{/each}
		{#if unscheduled.length}
			<section class="grid gap-3">
				<h3 class="font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase">{t('tp.tbd')}</h3>
				<div class="grid gap-3 sm:grid-cols-2">{#each unscheduled as x (x.id)}<GameCard tie={x} {path} me={d.team.slug} />{/each}</div>
			</section>
		{/if}
	</div>

	<aside class="grid min-w-0 content-start gap-5">
		{#if d.dressing_rooms.length}
			<section class="rounded-2xl border border-line bg-white p-5 shadow-sm">
				<h2 class="mb-3 flex items-center gap-2 font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase"><DoorOpen size={15} aria-hidden="true" /> {t('tp.team.dressing')}</h2>
				<ul class="grid gap-1 text-sm">
					{#each d.dressing_rooms as r (r.name)}
						<li class="flex flex-wrap justify-between gap-x-3"><b class="text-ink">{r.name}</b>{#if r.assigned_from}<span class="font-mono text-xs text-muted">{timeLabel(r.assigned_from)}{#if r.assigned_until} – {timeLabel(r.assigned_until)}{/if}</span>{/if}</li>
					{/each}
				</ul>
			</section>
		{/if}
		<section class="rounded-2xl border border-line bg-white p-5 shadow-sm">
			<h2 class="mb-3 flex items-center gap-2 font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase"><Shirt size={15} aria-hidden="true" /> {t('tp.team.roster')}</h2>
			{#if !d.roster.length}
				<p class="text-sm text-muted">{t('ts.no_roster')}</p>
			{:else}
				<ul class="grid gap-x-4 sm:grid-cols-2 lg:grid-cols-1">
					{#each players as p, i (i)}
						<li class="flex items-center gap-3 border-b border-rule py-1.5 text-sm last:border-0">
							<span class="w-7 text-right font-display text-base font-extrabold text-club tabular-nums">{p.jersey_number ?? ''}</span>
							<span class="min-w-0 flex-1 truncate font-semibold text-ink">{p.name}</span>
							{#if p.position_label}<span class="font-mono text-[11px] text-muted">{p.position_label}</span>{/if}
						</li>
					{/each}
				</ul>
				{#if staff.length}
					<h3 class="mt-4 mb-1 font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase">{t('tp.role.staff')}</h3>
					<ul class="grid gap-0.5 text-sm">{#each staff as p, i (i)}<li class="flex justify-between gap-3"><span class="text-ink">{p.name}</span>{#if p.position_label}<span class="font-mono text-[11px] text-muted">{p.position_label}</span>{/if}</li>{/each}</ul>
				{/if}
			{/if}
		</section>
	</aside>
</div>
