<script lang="ts">
	import { page } from '$app/state';
	/** Full-width underline tab bar in a card (the member profile's look), URL-synced: each tab links to `?tab=<key>`. */
	let { tabs, active, label }: { tabs: { key: string; label: string }[]; active: string; label: string } = $props();
</script>

<nav class="card flex flex-wrap items-center gap-x-3 px-3" aria-label={label}>
	<div class="flex flex-wrap">
		{#each tabs as item (item.key)}
			<a
				href="?tab={item.key}{page.url.searchParams.get('view') === 'coach' ? '&view=coach' : ''}"
				data-sveltekit-noscroll
				data-sveltekit-keepfocus
				aria-current={active === item.key ? 'page' : undefined}
				class="-mb-px border-b-2 px-4 py-3.5 font-display text-sm font-bold tracking-[.08em] whitespace-nowrap uppercase {active === item.key ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'}">{item.label}</a>
		{/each}
	</div>
</nav>
