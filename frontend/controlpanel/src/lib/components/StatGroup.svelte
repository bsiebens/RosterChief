<script lang="ts">
	import { CalendarDays, Contact, ShoppingCart, Users } from '@lucide/svelte';
	import type { StatGroup } from '#lib/clubs.ts';

	let { group }: { group: StatGroup } = $props();

	const icons = { contact: Contact, users: Users, 'calendar-days': CalendarDays, 'shopping-cart': ShoppingCart } as const;
	const Icon = $derived(icons[group.icon as keyof typeof icons] ?? Users);
</script>

<div class="card flex flex-col">
	<div class="flex items-center gap-2 border-b border-line px-4 py-3 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">
		<Icon size={16} aria-hidden="true" />
		{group.title}
		{#if !group.available}<span class="badge badge-ghost badge-sm ml-auto normal-case">not available yet</span>{/if}
	</div>
	<dl class="px-4 {group.available ? '' : 'opacity-60'}">
		{#each group.stats as stat (stat.label)}
			<div class="flex items-center justify-between py-2">
				<dt class="text-sm text-muted">{stat.label}</dt>
				<dd class="font-mono font-semibold text-ink tabular-nums">{stat.value ?? '—'}</dd>
			</div>
		{/each}
	</dl>
</div>
