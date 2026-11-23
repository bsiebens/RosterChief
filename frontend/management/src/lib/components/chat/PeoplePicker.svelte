<script lang="ts">
	import { Search, X } from '@lucide/svelte';
	import { onDestroy } from 'svelte';
	import { chatApi, showRoleLabel, type Person } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import PersonAvatar from './PersonAvatar.svelte';

	/**
	 * Search people (GET /chat/people, debounced) and pick them. `single` keeps one person; otherwise the picks pile up as removable chips.
	 * `exclude` hides people who are already in (member ids).
	 */
	let { selected = $bindable([]), single = false, exclude = [] }: { selected?: Person[]; single?: boolean; exclude?: string[] } = $props();

	let q = $state('');
	let results = $state<Person[]>([]);
	let busy = $state(false);
	let searched = $state(false);
	let timer: ReturnType<typeof setTimeout> | null = null;
	let ticket = 0;

	function search() {
		if (timer) clearTimeout(timer);
		const mine = ++ticket;
		timer = setTimeout(async () => {
			busy = true;
			const res = await chatApi.people(q.trim());
			if (mine !== ticket) return;
			busy = false;
			searched = true;
			results = res.ok ? (res.data?.results ?? []) : [];
		}, 250);
	}
	onDestroy(() => {
		if (timer) clearTimeout(timer);
		ticket++;
	});

	const picked = (p: Person) => selected.some((x) => x.member_id === p.member_id);
	const shown = $derived(results.filter((p) => !exclude.includes(p.member_id) && !(single && picked(p))));

	function toggle(p: Person) {
		if (single) selected = [p];
		else selected = picked(p) ? selected.filter((x) => x.member_id !== p.member_id) : [...selected, p];
	}
</script>

<div class="grid gap-2">
	{#if selected.length}
		<ul class="flex flex-wrap gap-1.5" aria-label={t('chat.new.selected', { n: selected.length })}>
			{#each selected as p (p.member_id)}
				<li class="inline-flex items-center gap-1.5 rounded-full border border-line bg-white py-0.5 pr-1 pl-0.5 text-xs text-ink">
					<PersonAvatar person={p} size="size-6" text="text-[10px]" />{p.name}
					<button type="button" class="grid size-5 place-items-center rounded-full text-muted hover:bg-paper" aria-label={t('chat.composer.remove', { name: p.name })} onclick={() => (selected = selected.filter((x) => x.member_id !== p.member_id))}><X size={12} aria-hidden="true" /></button>
				</li>
			{/each}
		</ul>
	{/if}
	<div class="relative">
		<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" aria-hidden="true" />
		<input class="input w-full pl-9" type="search" bind:value={q} oninput={search} onfocus={() => { if (!searched) search(); }} placeholder={t('chat.new.search')} aria-label={t('chat.new.search')} />
	</div>
	{#if searched}
		<ul class="max-h-56 divide-y divide-rule overflow-y-auto rounded-lg border border-line" aria-busy={busy}>
			{#each shown as p (p.member_id)}
				<li>
					<button type="button" class="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-paper" aria-pressed={picked(p)} onclick={() => toggle(p)}>
						<PersonAvatar person={p} />
						<span class="min-w-0 flex-1 truncate text-sm text-ink">{p.name}{#if showRoleLabel(p)} <span class="text-xs text-muted">· {p.role_label}</span>{/if}</span>
						{#if picked(p)}<span class="text-xs font-bold text-club">✓</span>{/if}
					</button>
				</li>
			{:else}
				<li class="px-3 py-3 text-sm text-muted">{busy ? t('chat.loading') : t('chat.new.no_results')}</li>
			{/each}
		</ul>
	{/if}
</div>
