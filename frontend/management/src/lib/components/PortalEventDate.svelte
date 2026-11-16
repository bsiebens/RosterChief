<script lang="ts">
	import { getLang } from '#lib/i18n.svelte.ts';

	/** A date tile: the weekday on a band in the club accent colour, the day large, the month small. */
	let { start, compact = false }: { start: string; compact?: boolean } = $props();
	const part = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), options).format(new Date(start));
</script>

<time datetime={start} class="grid {compact ? 'w-12' : 'w-14'} shrink-0 overflow-hidden rounded-box border border-line bg-white text-center shadow-xs">
	<span class="bg-club py-0.5 font-mono text-[10px] font-semibold tracking-[.12em] text-club-content uppercase">{part({ weekday: 'short' })}</span>
	<span class="font-display leading-none font-extrabold text-ink {compact ? 'pt-0.5 text-xl' : 'pt-1 text-[1.65rem]'}">{part({ day: 'numeric' })}</span>
	<span class="font-mono text-[10px] tracking-wide text-muted uppercase {compact ? 'pb-0.5' : 'pb-1.5'}">{part({ month: 'short' })}</span>
</time>
