<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { num } from '#lib/analytics.ts';
	import type { PlayerStats } from '#lib/member-stats.ts';
	import Tile from './Tile.svelte';

	/** The four headline rates of a skater: goals and points per game, current and longest point streak. */
	let { player }: { player: PlayerStats } = $props();
</script>

<div class="grid grid-cols-2 gap-2">
	<Tile value={num(player.goals_per_game, 2)} label={t('stats.gpg')} />
	<Tile value={num(player.points_per_game, 2)} label={t('stats.ppg')} />
	<Tile value={player.current_point_streak} label={t('stats.streak.current')} hint={t('stats.streak.help')} accent={player.current_point_streak >= 3} />
	<Tile value={player.longest_point_streak} label={t('stats.streak.longest')} hint={t('stats.streak.help')} />
</div>
