<script lang="ts">
	import { getLang } from '#lib/i18n.svelte.ts';

	/** A date tile: the weekday on a band in the club accent colour, the day large, the month small. */
	let { start }: { start: string } = $props();
	const part = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), options).format(new Date(start));
</script>

<time datetime={start} class="grid w-14 shrink-0 self-start overflow-hidden rounded-box border border-line bg-white text-center shadow-xs">
	<span class="bg-club py-0.5 font-mono text-[10px] font-semibold tracking-[.12em] text-club-content uppercase">{part({ weekday: 'short' })}</span>
	<span class="pt-1 font-display text-[1.65rem] leading-none font-extrabold text-ink">{part({ day: 'numeric' })}</span>
	<span class="pb-1.5 font-mono text-[10px] tracking-wide text-muted uppercase">{part({ month: 'short' })}</span>
</time>
