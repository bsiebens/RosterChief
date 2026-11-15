<script lang="ts">
	import { ArrowDown, ArrowUp, Plus, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import { HOCKEY_PRESET, type LineupGroup, type Sport } from '#lib/sports.ts';
	import Alert from '../Alert.svelte';

	let { sport, onchange }: { sport: Sport; onchange: () => Promise<void> | void } = $props();

	const copy = (t: LineupGroup[]): LineupGroup[] => JSON.parse(JSON.stringify(t ?? []));
	// svelte-ignore state_referenced_locally
	let enabled = $state(sport.lineup_slots_enabled);
	// svelte-ignore state_referenced_locally
	let groups = $state<LineupGroup[]>(copy(sport.lineup_template));
	let busy = $state(false);
	let error = $state('');
	let saved = $state(false);

	const dirty = $derived(enabled !== sport.lineup_slots_enabled || JSON.stringify(groups) !== JSON.stringify(sport.lineup_template ?? []));

	function move<T>(list: T[], i: number, d: -1 | 1) {
		const j = i + d;
		if (j < 0 || j >= list.length) return;
		[list[i], list[j]] = [list[j], list[i]];
	}
	const addGroup = () => groups.push({ key: '', label: '', slots: [{ key: '', label: '' }] });
	const preset = () => {
		groups = copy(HOCKEY_PRESET);
		saved = false;
	};

	async function save() {
		busy = true;
		error = '';
		saved = false;
		const r = await platform('PUT', `/sports/${sport.id}/lineup-slots`, { enabled, template: groups });
		busy = false;
		if (!r.ok) return (error = r.fields.template ?? r.message ?? 'The line-up lines could not be saved.');
		saved = true;
		await onchange();
	}
</script>

<div class="card">
	<div class="card-body gap-4">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Line-up lines</h2>
				<p class="mt-1 max-w-prose text-xs text-muted">When on, coaches of clubs in this sport can place picked players in the slots below (for example Line 1 · LW). Clubs cannot switch this on themselves. Slots are informational only and are not used for statistics.</p>
			</div>
			<label class="flex cursor-pointer items-center gap-2 text-sm font-semibold">
				<input type="checkbox" class="toggle toggle-sm" bind:checked={enabled} />
				Line-up lines
			</label>
		</div>

		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if saved && !dirty}<Alert kind="success">Saved.</Alert>{/if}

		<div class="grid gap-3">
			{#each groups as g, gi (gi)}
				<div class="rounded-box border border-line p-3">
					<div class="flex items-center gap-2">
						<input class="input input-bordered input-sm min-w-0 flex-1" aria-label="Group label" placeholder="Group, for example Line 1" maxlength="40" bind:value={g.label} />
						<button type="button" class="btn btn-ghost btn-sm btn-square" aria-label="Move group up" disabled={gi === 0} onclick={() => move(groups, gi, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
						<button type="button" class="btn btn-ghost btn-sm btn-square" aria-label="Move group down" disabled={gi === groups.length - 1} onclick={() => move(groups, gi, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
						<button type="button" class="btn btn-ghost btn-sm btn-square text-error" aria-label="Remove group" onclick={() => groups.splice(gi, 1)}><Trash2 size={14} aria-hidden="true" /></button>
					</div>
					<ul class="mt-2 grid gap-1.5 pl-3">
						{#each g.slots as s, si (si)}
							<li class="flex items-center gap-2">
								<input class="input input-bordered input-sm min-w-0 flex-1" aria-label="Slot label" placeholder="Slot, for example LW" maxlength="40" bind:value={s.label} />
								<button type="button" class="btn btn-ghost btn-xs btn-square" aria-label="Move slot up" disabled={si === 0} onclick={() => move(g.slots, si, -1)}><ArrowUp size={12} aria-hidden="true" /></button>
								<button type="button" class="btn btn-ghost btn-xs btn-square" aria-label="Move slot down" disabled={si === g.slots.length - 1} onclick={() => move(g.slots, si, 1)}><ArrowDown size={12} aria-hidden="true" /></button>
								<button type="button" class="btn btn-ghost btn-xs btn-square text-error" aria-label="Remove slot" onclick={() => g.slots.splice(si, 1)}><Trash2 size={12} aria-hidden="true" /></button>
							</li>
						{/each}
					</ul>
					<button type="button" class="btn btn-ghost btn-xs mt-2 gap-1" onclick={() => g.slots.push({ key: '', label: '' })}><Plus size={12} aria-hidden="true" /> Add slot</button>
				</div>
			{:else}
				<p class="text-sm text-muted">No groups yet.</p>
			{/each}
		</div>

		<div class="flex flex-wrap items-center gap-2">
			<button type="button" class="btn btn-outline btn-sm gap-1" onclick={addGroup}><Plus size={13} aria-hidden="true" /> Add group</button>
			<button type="button" class="btn btn-outline btn-sm" onclick={preset}>Use the ice hockey preset</button>
			<button type="button" class="btn btn-primary btn-sm ml-auto" disabled={busy || !dirty} onclick={save}>Save</button>
		</div>
	</div>
</div>
