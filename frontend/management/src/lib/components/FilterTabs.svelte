<script lang="ts">
	/**
	 * The dark pill tab group used in list filter bars (the members kind switch, the Settings tabs): each tab is a link, but
	 * a click navigates client-side through `onnavigate` so scroll position and focus are kept. `count` shows a small number
	 * badge (including 0, shown as 99+ from 100 up); `warn` makes it the warning colour when the count is above 0 (things waiting for someone).
	 */
	type Tab = { key: string; label: string; count?: number; warn?: boolean };
	let { tabs, active, label, hrefFor, onnavigate }: { tabs: Tab[]; active: string; label: string; hrefFor: (key: string) => string; onnavigate: (href: string) => void } = $props();
</script>

<div class="flex w-fit items-center gap-1 " role="group" aria-label={label}>
	{#each tabs as tab (tab.key)}
		<a
			href={hrefFor(tab.key)}
			aria-current={active === tab.key ? 'true' : undefined}
			onclick={(e) => {
				e.preventDefault();
				onnavigate(hrefFor(tab.key));
			}}
			class="flex items-center gap-1.5 rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {active === tab.key ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}"
			>{tab.label}{#if tab.count !== undefined}<span class="badge badge-sm font-mono {tab.warn && tab.count > 0 ? 'badge-warning' : active === tab.key ? 'border-white/30 bg-white/20 text-white' : 'border-line bg-surface text-slate'}">{tab.count > 99 ? '99+' : tab.count}</span>{/if}</a>
	{/each}
</div>
