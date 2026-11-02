<script lang="ts">
	import { Pencil, Plus, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { CatalogFeature } from '#lib/features.ts';
	import type { Competition, Sport } from '#lib/sports.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';
	import CompetitionForm from './CompetitionForm.svelte';

	let {
		competitions,
		defaultSportId,
		sports,
		features,
		onchange
	}: { competitions: Competition[]; defaultSportId: string; sports: Sport[]; features: CatalogFeature[]; onchange: () => Promise<void> | void } = $props();

	const featureName = (code: string) => features.find((f) => f.code === code)?.name ?? code;

	let formOpen = $state(false);
	let editing = $state<Competition | null>(null);
	let deleting = $state<Competition | null>(null);
	let notice = $state('');
	let error = $state('');
	let busy = $state(false);

	const saved = (m: string) => ((notice = m), onchange());

	async function remove() {
		if (!deleting) return;
		busy = true;
		error = '';
		const r = await platform('DELETE', `/competitions/${deleting.id}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The competition could not be deleted.');
		notice = `Competition “${deleting.name}” deleted.`;
		deleting = null;
		await onchange();
	}
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Competitions</h2>
			<button class="btn btn-primary btn-sm" onclick={() => ((editing = null), (formOpen = true))}><Plus size={14} aria-hidden="true" /> Add competition</button>
		</div>
		<p class="text-sm text-muted">
			Clubs can only use a competition when their plan or override includes its required feature. Plans and overrides are managed on <a class="link" href="/features">Features</a>.
		</p>
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
		{#if !competitions.length}
			<p class="rounded border border-dashed border-rule px-3 py-3 text-sm text-muted">No competitions yet</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>Name</th><th>Module</th><th>Required feature</th><th>Sport</th><th class="text-right"><span class="sr-only">Actions</span></th></tr>
					</thead>
					<tbody>
						{#each competitions as c (c.id)}
							<tr>
								<td class="font-semibold text-ink">{c.name}</td>
								<td class="font-mono text-xs">{c.module}</td>
								<td>{#if c.feature_code}<span class="badge badge-info normal-case" title={c.feature_code}>{featureName(c.feature_code)}</span>{:else}<span class="text-muted">every club</span>{/if}</td>
								<td>{c.sport_name ?? '—'}</td>
								<td class="text-right whitespace-nowrap">
									<button class="btn btn-ghost btn-xs" aria-label="Edit {c.name}" onclick={() => ((editing = c), (formOpen = true))}><Pencil size={14} aria-hidden="true" /></button>
									<button class="btn btn-ghost btn-xs text-club-dark" aria-label="Delete {c.name}" onclick={() => ((deleting = c), (error = ''))}><Trash2 size={14} aria-hidden="true" /></button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<CompetitionForm bind:open={formOpen} competition={editing} {defaultSportId} {sports} {features} onsaved={saved} />

<Modal open={!!deleting} title="Delete competition" onclose={() => (deleting = null)}>
	{#if deleting}
		<div class="flex flex-col gap-4">
			<p class="text-sm">Delete <strong>{deleting.name}</strong>? Clubs can no longer import fixtures from it.</p>
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="flex justify-end gap-2">
				<button class="btn btn-ghost btn-sm" onclick={() => (deleting = null)}>Cancel</button>
				<button class="btn btn-error btn-sm" disabled={busy} onclick={remove}>Delete competition</button>
			</div>
		</div>
	{/if}
</Modal>
