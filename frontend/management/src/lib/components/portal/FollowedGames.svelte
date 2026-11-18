<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { MapPin } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { formatDateTime, formatTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { FollowedGame, GameSheet } from '#lib/portal.ts';
	import EventBackdrop from './EventBackdrop.svelte';
	import GameTimelineDialog from './GameTimelineDialog.svelte';
	import { liveGames } from '#lib/live.svelte.ts';

	/**
	 * The games the person follows (live, coming up in the next day, or just finished) as small full-width cards on top of Home: both teams
	 * with their logos or initials, the score, where it is played and a LIVE badge while it is on. They update by themselves; a click opens the
	 * game's timeline as the competition captured it (and keeps refreshing it while the game is live).
	 */
	let { games: initial, crest = null, clubName = '', background = null, show: which = 'all' }: { /** `live`: only the games on now (several swipe sideways); `rest`: the others; `all`: everything in one list. */ show?: 'all' | 'live' | 'rest'; games: FollowedGame[]; crest?: string | null; clubName?: string; /** The club's event background: a live game sits on it (greyscale, darkened), with white text. */ background?: string | null } = $props();
	// svelte-ignore state_referenced_locally
	let games = $state(initial);
	$effect(() => {
		games = initial;
	});

	const anyLive = $derived(games.some((game) => game.status === 'live'));
	const shown = $derived(games.filter((game) => (which === 'all' ? true : which === 'live' ? game.status === 'live' : game.status !== 'live')));
	$effect(() => {
		if (which !== 'rest') liveGames.count = games.filter((game) => game.status === 'live').length;
	});
	// Several live games sit side by side: swipe, with a dot per game.
	const carousel = $derived(which === 'live' && shown.length > 1);
	let strip = $state<HTMLElement>();
	let slide = $state(0);
	const onscroll = () => {
		if (strip) slide = Math.round(strip.scrollLeft / (strip.scrollWidth / shown.length));
	};
	function goTo(index: number) {
		if (!strip || !shown.length) return;
		const next = (index + shown.length) % shown.length;
		const card = strip.children[next] as HTMLElement | undefined;
		if (card) strip.scrollTo({ left: card.offsetLeft - (strip.clientWidth - card.offsetWidth) / 2, behavior: 'smooth' });
	}
	// Moves on by itself every few seconds; stops for good once the person touches or hovers it.
	let paused = $state(false);
	let resume: ReturnType<typeof setTimeout>;
	// Any touch, hover or tap holds the countdown; after 8 quiet seconds it starts again from the current game.
	function touch() {
		paused = true;
		clearTimeout(resume);
		resume = setTimeout(() => (paused = false), 8000);
	}
	onDestroy(() => clearTimeout(resume));
	$effect(() => {
		if (!carousel || paused) return;
		const id = setTimeout(() => goTo(slide + 1), 6000);
		return () => clearTimeout(id);
	});
	async function refresh() {
		if (document.hidden) return;
		const result = await api<FollowedGame[]>('GET', '/portal/followed');
		if (result.ok && result.data) games = result.data;
		if (opened) await loadGame(opened.event_id, false);
	}
	onMount(() => {
		let timer: ReturnType<typeof setTimeout>;
		const loop = () => {
			timer = setTimeout(async () => {
				await refresh();
				loop();
			}, anyLive ? 15_000 : 90_000);
		};
		loop();
		return () => clearTimeout(timer);
	});

	let opened = $state<FollowedGame | null>(null);
	let sheet = $state<GameSheet | null>(null);
	let open = $state(false);
	let missing = $state(false);
	async function loadGame(id: string, show = true) {
		const result = await api<{ game: GameSheet | null }>('GET', `/portal/events/${id}/game`);
		if (result.ok) {
			sheet = result.data?.game ?? null;
			missing = !sheet;
			if (show && sheet) open = true;
		}
	}
	async function show(game: FollowedGame) {
		opened = game;
		sheet = null;
		missing = false;
		await loadGame(game.event_id);
	}
	$effect(() => {
		if (!open) opened = null;
	});

	// Home team on the left, like a scoreboard; the score is ours-theirs in the data.
	const sides = (game: FollowedGame) => {
		const ours = { name: game.teams || clubName, logo: crest, mine: true, score: game.score_for };
		const theirs = { name: game.opponent || t('portal.opponent'), logo: game.opponent_logo, mine: false, score: game.score_against };
		return game.is_home ? [ours, theirs] : [theirs, ours];
	};
</script>

{#if shown.length}
	<section bind:this={strip} {onscroll} onpointerdown={touch} onpointermove={touch} onwheel={touch} class={carousel ? 'flex min-w-0 snap-x snap-mandatory gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden' : 'grid gap-2'} aria-label={t('portal.following')}>
		{#each shown as game, index (game.event_id)}
			{@const [left, right] = sides(game)}
			{@const live = game.status === 'live'}
			{@const ink = live ? 'text-[#fff]' : 'text-ink'}
			<button type="button" class="card relative isolate grid {carousel ? 'w-[88%] shrink-0 snap-center' : 'w-full'} min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 overflow-hidden border-l-4 px-3 text-left transition-colors md:gap-3 md:px-4 {live ? 'portal-event-banner min-h-32 py-4 text-[#fff]' : 'py-2.5 hover:bg-subhead border-l-ice'}" onclick={() => (carousel && index !== slide ? (touch(), goTo(index)) : show(game))} aria-label={t('portal.timeline_view') + ': ' + game.title}>
				{#if live}<EventBackdrop src={background} />{/if}
				<span class="flex min-w-0 flex-col-reverse items-center gap-1 md:flex-row md:justify-end md:gap-3">
					<span class="line-clamp-2 min-w-0 text-center font-display text-sm leading-tight font-extrabold break-words {ink} uppercase md:truncate md:text-right md:text-lg">{left.name}</span>
					<ClubCrest name={left.name} logoUrl={left.logo} size={live ? 52 : 40} />
				</span>
				<span class="grid justify-items-center gap-0.5 md:min-w-24">
					{#if game.status === 'scheduled'}
						<span class="font-display text-3xl leading-none font-extrabold {ink} tabular-nums">{formatTime(game.start)}</span>
					{:else}
						<span class="font-display {live ? 'text-5xl' : 'text-3xl'} leading-none font-extrabold tabular-nums {ink}">{left.score ?? '–'}<span class="px-1.5 {live ? 'text-[#fff]/70' : 'text-muted'}">:</span>{right.score ?? '–'}</span>
					{/if}
					{#if game.status === 'live'}
						<span class="inline-flex items-center gap-1.5 rounded-full bg-club px-2 py-0.5 font-mono text-[10px] font-bold tracking-wide text-club-content uppercase"><span class="size-1.5 animate-pulse rounded-full bg-current"></span>{t('portal.live')}</span>
					{:else}
						<span class="font-mono text-[10px] tracking-wide {live ? 'text-[#fff]/80' : 'text-muted'} uppercase">{game.status === 'final' ? t('portal.final') : new Date(game.start).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</span>
					{/if}
				</span>
				<span class="flex min-w-0 flex-col items-center gap-1 md:flex-row md:gap-3">
					<ClubCrest name={right.name} logoUrl={right.logo} size={live ? 52 : 40} />
					<span class="min-w-0">
						<span class="line-clamp-2 block text-center font-display text-sm leading-tight font-extrabold break-words {ink} uppercase md:truncate md:text-left md:text-lg">{right.name}</span>
						{#if game.location}<span class="hidden items-center gap-1 truncate text-xs {live ? 'text-[#fff]/80' : 'text-muted'} md:flex"><MapPin size={12} class="shrink-0" aria-hidden="true" />{game.location}</span>{/if}
					</span>
				</span>
			</button>
		{/each}
	</section>
	{#if carousel}
		<div class="flex items-center justify-center gap-2">
			{#each shown as game, i (game.event_id)}
				<button type="button" class="relative h-3 overflow-hidden rounded-full transition-all {i === slide ? 'w-10 bg-line' : 'w-3 bg-line'}" onclick={() => { touch(); goTo(i); }} aria-label={game.title}>
					{#if i === slide}{#key `${slide}-${paused}`}<span class="absolute inset-y-0 left-0 rounded-full bg-club {paused ? 'w-full' : 'dot-countdown'}"></span>{/key}{/if}
				</button>
			{/each}
		</div>
	{/if}
{/if}

{#if opened && sheet}
	<GameTimelineDialog game={sheet} title={opened.title} {crest} bind:open />
{/if}
{#if opened && missing}
	<p class="sr-only" role="status">{t('portal.no_timeline')}</p>
{/if}
