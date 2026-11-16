<script lang="ts">
	import { Search, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';

	/** Search the club's members with an active membership and pick one (hidden input `name`). `exclude` hides people already on the team. */
	let { name, seasonId = '', exclude = [], value = $bindable(''), error = '' }: { name: string; seasonId?: string; exclude?: string[]; value?: string; error?: string } = $props();

	type Hit = { id: string; first_name: string; last_name: string; email: string; photo_url: string | null };
	let q = $state('');
	let hits = $state<Hit[]>([]);
	let picked = $state<Hit | null>(null);
	let loaded = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let seq = 0;

	async function search(text: string) {
		const mine = ++seq;
		const params = new URLSearchParams({ role: 'member', status: 'active', limit: '30', q: text });
		if (seasonId) params.set('season', seasonId);
		try {
			const res = await fetch(`/api/v1/members?${params}`, { headers: { accept: 'application/json' } });
			if (!res.ok || mine !== seq) return;
			hits = (((await res.json()).items ?? []) as Hit[]).filter((hit) => !exclude.includes(hit.id));
		} catch {
			if (mine === seq) hits = [];
		} finally {
			if (mine === seq) loaded = true;
		}
	}
	$effect(() => {
		search('');
		return () => clearTimeout(timer);
	});
	function onInput() {
		clearTimeout(timer);
		timer = setTimeout(() => search(q.trim()), 250);
	}
	function pick(hit: Hit | null) {
		picked = hit;
		value = hit?.id ?? '';
	}
</script>

<div class="grid min-w-0 gap-2">
	<input type="hidden" {name} {value} />
	{#if picked}
		<div class="flex min-h-12 items-center gap-3 rounded-box border border-line px-3 py-2">
			<Avatar first_name={picked.first_name} last_name={picked.last_name} photoUrl={picked.photo_url} size="size-9" text="text-xs" />
			<span class="min-w-0 flex-1"><b class="block truncate text-sm text-ink">{picked.first_name} {picked.last_name}</b><span class="block truncate text-xs text-muted">{picked.email}</span></span>
			<button type="button" class="btn btn-ghost btn-square btn-sm" aria-label={t('coach.pick_clear')} onclick={() => pick(null)}><X size={16} aria-hidden="true" /></button>
		</div>
	{:else}
		<label class="relative block"><span class="sr-only">{t('coach.pick_search')}</span><Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" /><input class="input w-full pl-9" type="search" placeholder={t('coach.pick_search')} bind:value={q} oninput={onInput} autocomplete="off" /></label>
		<ul class="max-h-64 divide-y divide-rule overflow-y-auto rounded-box border border-line">
			{#each hits as hit (hit.id)}
				<li>
					<button type="button" class="flex min-h-12 w-full items-center gap-3 px-3 py-2 text-left hover:bg-subhead" onclick={() => pick(hit)}>
						<Avatar first_name={hit.first_name} last_name={hit.last_name} photoUrl={hit.photo_url} size="size-9" text="text-xs" />
						<span class="min-w-0 flex-1"><b class="block truncate text-sm text-ink">{hit.last_name}, {hit.first_name}</b><span class="block truncate text-xs text-muted">{hit.email}</span></span>
					</button>
				</li>
			{:else}
				<li class="px-3 py-4 text-center text-sm text-muted">{loaded ? t('coach.pick_none') : t('common.loading')}</li>
			{/each}
		</ul>
		<p class="text-xs text-muted">{t('coach.pick_hint')}</p>
	{/if}
	{#if error}<span class="text-xs text-error">{error}</span>{/if}
</div>
