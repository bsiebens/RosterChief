<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';

	/** The marker of a game that is being played right now: a pill with a pulsing dot. `lg` for the game screen. */
	let { size = 'sm' }: { size?: 'sm' | 'lg' } = $props();
	const big = $derived(size === 'lg');
</script>

<span class="inline-flex items-center gap-2 rounded-full border border-danger/30 overflow-hidden bg-danger/10 font-display leading-none font-bold tracking-[.14em] text-danger uppercase {big ? 'h-8 px-4 text-sm' : 'h-5 px-2.5 text-[10px]'}">
	<span class="relative flex shrink-0 items-center justify-center {big ? 'size-3' : 'size-2'}">
		<span class="absolute inline-flex size-full animate-ping rounded-full bg-danger opacity-60"></span>
		<span class="relative inline-flex rounded-full bg-danger {big ? 'size-3' : 'size-2'}"></span>
	</span>
	<span class="pt-px">{t('tn.game.live')}</span>
</span>
