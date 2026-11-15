<script lang="ts">
	import { onMount } from 'svelte';
	import { MapPin } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { formatDateTime, formatTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { FollowedGame, GameSheet } from '#lib/portal.ts';
	import GameTimelineDialog from './GameTimelineDialog.svelte';

	/**
	 * The games the person follows (live, coming up in the next day, or just finished) as small full-width cards on top of Home: both teams
	 * with their logos or initials, the score, where it is played and a LIVE badge while it is on. They update by themselves; a click opens the
	 * game's timeline as the competition captured it (and keeps refreshing it while the game is live).
	 */
	let { games: initial, crest = null, clubName = '' }: { games: FollowedGame[]; crest?: string | null; clubName?: string } = $props();
	// svelte-ignore state_referenced_locally
	let games = $state(initial);
	$effect(() => {
		games = initial;
	});

	const anyLive = $derived(games.some((game) => game.status === 'live'));
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

{#if games.length}
	<section class="grid gap-2" aria-label={t('portal.following')}>
		{#each games as game (game.event_id)}
			{@const [left, right] = sides(game)}
			<button type="button" class="card grid w-full grid-cols-[1fr_auto_1fr] items-center gap-3 border-l-4 px-4 py-2.5 text-left transition-colors hover:bg-subhead {game.status === 'live' ? 'border-l-club' : 'border-l-ice'}" onclick={() => show(game)} aria-label={t('portal.timeline_view') + ': ' + game.title}>
				<span class="flex min-w-0 items-center justify-end gap-3">
					<span class="min-w-0 truncate text-right font-display text-lg leading-tight font-extrabold text-ink uppercase">{left.name}</span>
					<ClubCrest name={left.name} logoUrl={left.logo} size={40} />
				</span>
				<span class="grid min-w-24 justify-items-center gap-0.5">
					{#if game.status === 'scheduled'}
						<span class="font-display text-3xl leading-none font-extrabold text-ink tabular-nums">{formatTime(game.start)}</span>
					{:else}
						<span class="font-display text-3xl leading-none font-extrabold tabular-nums {game.status === 'live' ? 'text-club-dark' : 'text-ink'}">{left.score ?? '–'}<span class="px-1.5 text-muted">:</span>{right.score ?? '–'}</span>
					{/if}
					{#if game.status === 'live'}
						<span class="inline-flex items-center gap-1.5 rounded-full bg-club px-2 py-0.5 font-mono text-[10px] font-bold tracking-wide text-club-content uppercase"><span class="size-1.5 animate-pulse rounded-full bg-current"></span>{t('portal.live')}</span>
					{:else}
						<span class="font-mono text-[10px] tracking-wide text-muted uppercase">{game.status === 'final' ? t('portal.final') : formatDateTime(game.start)}</span>
					{/if}
				</span>
				<span class="flex min-w-0 items-center gap-3">
					<ClubCrest name={right.name} logoUrl={right.logo} size={40} />
					<span class="min-w-0">
						<span class="block truncate font-display text-lg leading-tight font-extrabold text-ink uppercase">{right.name}</span>
						{#if game.location}<span class="flex items-center gap-1 truncate text-xs text-muted"><MapPin size={12} class="shrink-0" aria-hidden="true" />{game.location}</span>{/if}
					</span>
				</span>
			</button>
		{/each}
	</section>
{/if}

{#if opened && sheet}
	<GameTimelineDialog game={sheet} title={opened.title} {crest} bind:open />
{/if}
{#if opened && missing}
	<p class="sr-only" role="status">{t('portal.no_timeline')}</p>
{/if}
