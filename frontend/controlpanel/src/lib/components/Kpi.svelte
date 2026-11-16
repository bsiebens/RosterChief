<script lang="ts">
	let { label, value, sub, tone = 'none' }: { label: string; value: string | number; sub?: string; tone?: 'none' | 'warn' | 'club' } = $props();
</script>

<div class="card p-3.5 {tone === 'warn' ? 'border-warn' : tone === 'club' ? 'border-club' : ''}">
	<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">{label}</div>
	<div class="mt-0.5 font-display text-4xl leading-none font-extrabold tabular-nums {tone === 'warn' ? 'text-warn' : tone === 'club' ? 'text-club' : 'text-ink'}">{value}</div>
	{#if sub}<div class="mt-1.5 font-mono text-[11px] text-muted">{sub}</div>{/if}
</div>
