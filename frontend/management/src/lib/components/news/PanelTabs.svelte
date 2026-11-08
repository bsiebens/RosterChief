<script lang="ts">
	/**
	 * Pill tabs that switch panels on the same page (same look as `Tabs.svelte`, which links to `?tab=`). The panels stay
	 * mounted and are only hidden, so a form inside a hidden panel keeps its state and still submits.
	 */
	let { tabs, active = $bindable(), label, idBase }: { tabs: { key: string; label: string }[]; active: string; label: string; idBase: string } = $props();

	const keys = (ev: KeyboardEvent) => {
		const i = tabs.findIndex((x) => x.key === active);
		const to = ev.key === 'ArrowRight' ? (i + 1) % tabs.length : ev.key === 'ArrowLeft' ? (i - 1 + tabs.length) % tabs.length : ev.key === 'Home' ? 0 : ev.key === 'End' ? tabs.length - 1 : -1;
		if (to < 0) return;
		ev.preventDefault();
		active = tabs[to].key;
		document.getElementById(`${idBase}-tab-${active}`)?.focus();
	};
</script>

<div class="flex w-fit items-center gap-1 rounded-full bg-primary p-1" role="tablist" aria-label={label}>
	{#each tabs as tab (tab.key)}
		<button
			type="button"
			role="tab"
			id="{idBase}-tab-{tab.key}"
			aria-selected={active === tab.key}
			aria-controls="{idBase}-panel-{tab.key}"
			tabindex={active === tab.key ? 0 : -1}
			onclick={() => (active = tab.key)}
			onkeydown={keys}
			class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-club {active === tab.key ? 'bg-primary-content text-primary' : 'text-primary-dim hover:text-primary-content'}">{tab.label}</button>
	{/each}
</div>
