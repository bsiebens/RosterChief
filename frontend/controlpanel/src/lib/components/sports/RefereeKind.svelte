<script lang="ts">
	import { ArrowDown, ArrowUp, Check, Pencil, Plus, Trash2, X } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { RefereeKind, RefereeType } from '#lib/sports.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { sportId, kind, items, onchange }: { sportId: string; kind: RefereeKind; items: RefereeType[]; onchange: () => Promise<void> | void } = $props();

	const word = $derived(kind === 'referee' ? 'referee' : 'official');
	const title = $derived(kind === 'referee' ? 'Referees' : 'Officials');

	// Optimistic order while a reorder is in flight (ids), null = show the server's order.
	let override = $state<string[] | null>(null);
	const shown = $derived(override ? override.map((id) => items.find((t) => t.id === id)).filter((t): t is RefereeType => !!t) : items);

	let error = $state('');
	let busy = $state(false);
	let editing = $state<string | null>(null);
	let editName = $state('');
	let adding = $state('');
	let deleting = $state<RefereeType | null>(null);
	let deleteError = $state('');

	async function move(i: number, d: -1 | 1) {
		const ids = shown.map((t) => t.id);
		const j = i + d;
		if (j < 0 || j >= ids.length || busy) return;
		[ids[i], ids[j]] = [ids[j], ids[i]];
		override = ids;
		error = '';
		busy = true;
		const r = await platform('POST', `/sports/${sportId}/referee-types/reorder`, { kind, ids });
		if (!r.ok) {
			override = null;
			error = r.message ?? 'The order could not be saved.';
		} else {
			await onchange();
			override = null;
		}
		busy = false;
	}

	function startEdit(t: RefereeType) {
		editing = t.id;
		editName = t.name;
		error = '';
	}

	async function saveEdit(t: RefereeType) {
		const n = editName.trim();
		if (!n || n === t.name) return (editing = null);
		busy = true;
		const r = await platform('PATCH', `/referee-types/${t.id}`, { name: n });
		busy = false;
		if (!r.ok) return (error = r.fields.name ?? r.message ?? 'The name could not be saved.');
		editing = null;
		error = '';
		await onchange();
	}

	async function add(event: SubmitEvent) {
		event.preventDefault();
		const n = adding.trim();
		if (!n) return;
		busy = true;
		error = '';
		const r = await platform('POST', `/sports/${sportId}/referee-types`, { kind, name: n });
		busy = false;
		if (!r.ok) return (error = r.fields.name ?? r.message ?? 'The type could not be added.');
		adding = '';
		await onchange();
	}

	async function remove() {
		if (!deleting) return;
		busy = true;
		deleteError = '';
		const r = await platform('DELETE', `/referee-types/${deleting.id}`);
		busy = false;
		if (!r.ok) return (deleteError = r.message ?? 'The type could not be deleted.');
		deleting = null;
		await onchange();
	}

	const inputId = $props.id();
</script>

<section class="flex flex-col gap-2" aria-label={title}>
	<h3 class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">{title}</h3>
	{#if error}<Alert kind="error">{error}</Alert>{/if}
	{#if shown.length}
		<ol class="divide-y divide-rule rounded border border-rule">
			{#each shown as t, i (t.id)}
				<li class="flex items-center gap-2 px-2.5 py-1.5">
					{#if editing === t.id}
						<input
							class="input input-bordered input-sm min-w-0 flex-1"
							aria-label="Rename {t.name}"
							bind:value={editName}
							onkeydown={(e) => {
								if (e.key === 'Enter') (e.preventDefault(), saveEdit(t));
								if (e.key === 'Escape') editing = null;
							}}
						/>
						<button class="btn btn-ghost btn-xs" aria-label="Save name" disabled={busy} onclick={() => saveEdit(t)}><Check size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-xs" aria-label="Cancel rename" onclick={() => (editing = null)}><X size={14} aria-hidden="true" /></button>
					{:else}
						<span class="min-w-0 flex-1 truncate text-sm text-ink">{t.name}</span>
						<button class="btn btn-ghost btn-xs" aria-label="Move {t.name} up" disabled={i === 0 || busy} onclick={() => move(i, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-xs" aria-label="Move {t.name} down" disabled={i === shown.length - 1 || busy} onclick={() => move(i, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-xs" aria-label="Rename {t.name}" onclick={() => startEdit(t)}><Pencil size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-xs text-club-dark" aria-label="Delete {t.name}" onclick={() => ((deleting = t), (deleteError = ''))}><Trash2 size={14} aria-hidden="true" /></button>
					{/if}
				</li>
			{/each}
		</ol>
	{:else}
		<p class="rounded border border-dashed border-rule px-3 py-3 text-sm text-muted">No {word} types yet</p>
	{/if}
	<form class="flex gap-2" onsubmit={add}>
		<label class="sr-only" for="{inputId}-add">New {word} type</label>
		<input id="{inputId}-add" class="input input-bordered input-sm min-w-0 flex-1" placeholder="New {word} type" autocomplete="off" bind:value={adding} />
		<button class="btn btn-outline btn-sm" disabled={busy || !adding.trim()}><Plus size={14} aria-hidden="true" /> Add {word} type</button>
	</form>
</section>

<Modal open={!!deleting} title="Delete {word} type" onclose={() => (deleting = null)}>
	{#if deleting}
		<div class="flex flex-col gap-4">
			<p class="text-sm">Delete <strong>{deleting.name}</strong>?</p>
			{#if deleteError}<Alert kind="error">{deleteError}</Alert>{/if}
			<div class="flex justify-end gap-2">
				<button class="btn btn-ghost btn-sm" onclick={() => (deleting = null)}>Cancel</button>
				<button class="btn btn-error btn-sm" disabled={busy} onclick={remove}>Delete</button>
			</div>
		</div>
	{/if}
</Modal>
