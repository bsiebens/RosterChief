<script lang="ts">
	/**
	 * Tabs that switch panels on the same page (the look of `TabBar`, which links to `?tab=`). The panels stay
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

<!-- The underline bar in a card, like the event and member pages (`TabBar`), but switching client-side. -->
<div class="card flex flex-wrap items-center gap-x-3 px-3" role="tablist" aria-label={label}>
	<div class="flex flex-wrap">
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
				class="-mb-px border-b-2 px-4 py-3.5 font-display text-sm font-bold tracking-[.08em] whitespace-nowrap uppercase focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-club {active === tab.key ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'}">{tab.label}</button>
		{/each}
	</div>
</div>
