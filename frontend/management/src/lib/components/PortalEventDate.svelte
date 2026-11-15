<script lang="ts">
	import { getLang } from '#lib/i18n.svelte.ts';

	let { start }: { start: string } = $props();
	const part = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), options).format(new Date(start));
</script>

<time datetime={start} class="grid min-w-12 shrink-0 place-items-center gap-0.5 self-start rounded-md bg-subhead px-2 py-1.5 text-center">
	<span class="font-mono text-[10px] uppercase text-muted">{part({ weekday: 'short' })}</span>
	<span class="font-display text-lg leading-none font-extrabold text-ink">{part({ day: 'numeric' })}</span>
	<span class="font-mono text-[10px] uppercase text-muted">{part({ month: 'short' })}</span>
</time>
