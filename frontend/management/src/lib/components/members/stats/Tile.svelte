<script lang="ts">
	/** One big number with a caption: the stat tiles of every section. */
	let { value, label, hint = '', accent = false }: { value: string | number; label: string; hint?: string; accent?: boolean } = $props();
</script>

<div class="rounded-lg bg-paper px-3 py-2.5 text-center" title={hint || undefined}>
	<p class="font-display text-2xl leading-none font-extrabold {accent ? 'text-club-dark' : 'text-ink'}">{value}</p>
	<p class="mt-1 text-[11px] text-muted">{label}</p>
</div>
