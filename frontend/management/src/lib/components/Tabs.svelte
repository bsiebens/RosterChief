<script lang="ts">
	/** Pill tab bar (same look as the members kind switch), URL-synced: each tab is a link to `?tab=<key>`. */
	let { tabs, active, label }: { tabs: { key: string; label: string }[]; active: string; label: string } = $props();
</script>

<div class="flex w-fit items-center gap-1 " role="group" aria-label={label}>
	{#each tabs as tab (tab.key)}
		<a
			href="?tab={tab.key}"
			data-sveltekit-noscroll
			data-sveltekit-keepfocus
			aria-current={active === tab.key ? 'true' : undefined}
			class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {active === tab.key ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{tab.label}</a>
	{/each}
</div>
