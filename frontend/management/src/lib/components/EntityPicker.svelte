<script lang="ts">
	import { Search, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Search-as-you-type picker for a member or a family. Searches the API from the browser (through the proxy) and
	 * submits the chosen id under `name` (hidden input). `exclude` hides ids that are already taken.
	 */
	let {
		kind,
		name,
		label,
		exclude = [],
		value = $bindable(''),
		error,
		extra = '',
		compact = false
	}: { kind: 'member' | 'family'; name: string; label: string; exclude?: string[]; value?: string; error?: string; extra?: string; compact?: boolean } = $props();

	type Hit = { id: string; label: string; sub?: string };
	let q = $state('');
	let hits = $state<Hit[]>([]);
	let picked = $state<Hit | null>(null);
	let focused = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let field: HTMLInputElement | undefined = $state();
	let pos = $state('');
	let seq = 0;
	const id = $props.id();

	async function search(text: string) {
		const mine = ++seq;
		const url = kind === 'member' ? `/api/v1/members?${extra || 'include_guardians=true'}&limit=8&q=${encodeURIComponent(text)}` : `/api/v1/members/families?limit=8&q=${encodeURIComponent(text)}`;
		try {
			const res = await fetch(url, { headers: { accept: 'application/json' } });
			if (!res.ok || mine !== seq) return;
			const json = await res.json();
			const items: any[] = json.items ?? [];
			hits = items
				.filter((i) => !exclude.includes(i.id))
				.map((i) => (kind === 'member' ? { id: i.id, label: `${i.first_name} ${i.last_name}`.trim(), sub: i.email } : { id: i.id, label: i.display_name }));
		} catch {
			hits = [];
		}
	}
	const MAX_H = 224;
	function place() {
		if (!field) return;
		const r = field.getBoundingClientRect();
		const below = window.innerHeight - r.bottom - 12;
		const above = r.top - 12;
		const up = below < 140 && above > below;
		const h = Math.min(MAX_H, up ? above : below);
		pos = `left:${r.left}px;width:${r.width}px;max-height:${h}px;` + (up ? `bottom:${window.innerHeight - r.top + 4}px;` : `top:${r.bottom + 4}px;`);
	}
	$effect(() => {
		if (!focused) return;
		place();
		const on = () => place();
		window.addEventListener('resize', on);
		window.addEventListener('scroll', on, true);
		return () => {
			window.removeEventListener('resize', on);
			window.removeEventListener('scroll', on, true);
		};
	});
	$effect(() => {
		hits.length;
		if (focused) place();
	});
	function onInput() {
		clearTimeout(timer);
		timer = setTimeout(() => search(q.trim()), 250);
	}
	function pick(h: Hit) {
		picked = h;
		value = h.id;
		q = '';
		hits = [];
	}
	function clear() {
		picked = null;
		value = '';
	}
</script>

<div class="form-control w-full">
	<label class={compact ? 'sr-only' : 'label'} for={id}><span class="label-text">{label}</span></label>
	<input type="hidden" {name} {value} />
	{#if picked}
		<div class="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2">
			<span class="font-semibold text-ink">{picked.label}</span>
			<button class="btn btn-ghost btn-xs" type="button" aria-label={t('common.clear')} onclick={clear}><X size={14} /></button>
		</div>
	{:else}
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input
				{id}
				bind:this={field}
				type="search"
				class="input input-bordered w-full pl-9 {error ? 'input-error' : ''}"
				placeholder={t(kind === 'member' ? 'families.picker.member' : 'families.picker.family')}
				autocomplete="off"
				bind:value={q}
				oninput={onInput}
				onfocus={() => {
					focused = true;
					if (!hits.length) search(q.trim());
				}}
				onblur={() => setTimeout(() => (focused = false), 150)}
				onkeydown={(e) => {
					if (e.key === 'Enter') {
						e.preventDefault();
						if (hits.length === 1) pick(hits[0]);
					}
				}}
			/>
		</div>
		{#if focused && (hits.length || q.trim())}
			<!-- Fixed to the viewport so the dialog's scroll box cannot clip it; flips above the field when there is no room below. -->
			<div class="fixed z-50 overflow-auto rounded-lg border border-line bg-white shadow-lg" style={pos}>
				{#if hits.length}
					<ul class="divide-y divide-rule">
						{#each hits as h (h.id)}
							<li>
								<button class="flex w-full flex-col items-start px-3 py-2 text-left hover:bg-surface focus:bg-surface focus:outline-none" type="button" onmousedown={(e) => e.preventDefault()} onclick={() => pick(h)}>
									<span class="text-sm font-semibold text-ink">{h.label}</span>
									{#if h.sub}<span class="text-xs text-muted">{h.sub}</span>{/if}
								</button>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="px-3 py-2 text-sm text-muted">{t('families.picker.none')}</p>
				{/if}
			</div>
		{/if}
	{/if}
	{#if error}<span class="mt-1 block text-xs text-error">{error}</span>{/if}
</div>
