<script lang="ts">
	import { X } from '@lucide/svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import type { Ref } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Several members as removable chips, added one at a time with the member EntityPicker. `exclude` hides ids that
	 * are already used elsewhere (e.g. in the other list). The picker only finds the members the user may see.
	 */
	let { label, help, items = $bindable([]), exclude = [], error }: { label: string; help?: string; items: Ref[]; exclude?: string[]; error?: string } = $props();

	let picked = $state('');
	let round = $state(0);
	const taken = $derived([...items.map((m) => m.id), ...exclude]);

	async function nameOf(id: string): Promise<string> {
		try {
			const res = await fetch(`/api/v1/members/${id}`, { headers: { accept: 'application/json' } });
			if (!res.ok) return '…';
			const m = await res.json();
			return `${m.first_name ?? ''} ${m.last_name ?? ''}`.trim() || '…';
		} catch {
			return '…';
		}
	}

	$effect(() => {
		const id = picked;
		if (!id) return;
		picked = '';
		round++; // fresh, empty picker for the next one
		if (items.some((m) => m.id === id)) return;
		const entry = { id, name: '…' };
		items = [...items, entry];
		nameOf(id).then((name) => (items = items.map((m) => (m.id === id ? { ...m, name } : m))));
	});
</script>

<div class="grid gap-2">
	{#key round}
		<EntityPicker kind="member" name="" {label} exclude={taken} bind:value={picked} {error} />
	{/key}
	{#if items.length}
		<ul class="flex flex-wrap gap-1.5" aria-label={label}>
			{#each items as m (m.id)}
				<li class="inline-flex items-center gap-1 rounded-full border border-line bg-subhead py-0.5 pr-1 pl-3 text-sm text-ink">
					{m.name}
					<button type="button" class="btn btn-ghost btn-xs btn-square rounded-full" aria-label={t('eventform.members.remove', { name: m.name })} onclick={() => (items = items.filter((x) => x.id !== m.id))}><X size={13} /></button>
				</li>
			{/each}
		</ul>
	{/if}
	{#if help}<span class="text-xs text-muted">{help}</span>{/if}
</div>
