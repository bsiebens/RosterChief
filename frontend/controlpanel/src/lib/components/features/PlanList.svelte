<script lang="ts">
	import { Pencil, Plus, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import { limitText, type CatalogFeature, type FullPlan } from '#lib/features.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';
	import PlanForm from './PlanForm.svelte';

	let { plans, catalog, canEdit, onchange }: { plans: FullPlan[]; catalog: CatalogFeature[]; canEdit: boolean; onchange: () => void } = $props();

	// Feature chips shown per card before "+N more"; the rest expand in place so cards stay compact as the catalog grows.
	const VISIBLE = 6;
	let expanded = $state<Record<string, boolean>>({});
	const shownFeatures = (p: FullPlan) => (expanded[p.id] ? p.features : p.features.slice(0, VISIBLE));

	// Up to 3 plans share one full-width row. Beyond that, rows of at most 3, balanced (4 -> 2+2, 5 -> 3+2, 7 -> 3+2+2),
	// with the earlier rows taking the extras; every row stretches its cards across the full width.
	const rows = $derived.by(() => {
		const n = plans.length;
		if (!n) return [];
		const count = Math.ceil(n / 3);
		const base = Math.floor(n / count);
		const extra = n % count;
		const out: FullPlan[][] = [];
		let i = 0;
		for (let r = 0; r < count; r++) {
			const size = base + (r < extra ? 1 : 0);
			out.push(plans.slice(i, i + size));
			i += size;
		}
		return out;
	});

	let formOpen = $state(false);
	let editing = $state<FullPlan | null>(null);
	let deleting = $state<FullPlan | null>(null);
	let notice = $state('');
	let error = $state('');
	let busy = $state(false);

	const create = () => ((editing = null), (formOpen = true));
	const edit = (p: FullPlan) => ((editing = p), (formOpen = true));
	const saved = (m: string) => ((notice = m), (error = ''), onchange());

	async function remove() {
		if (!deleting) return;
		busy = true;
		error = '';
		const r = await platform('DELETE', `/plans/${deleting.id}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The plan could not be deleted.');
		notice = `Plan “${deleting.name}” deleted.`;
		deleting = null;
		onchange();
	}
</script>

<div class="flex flex-col gap-4">
	<div class="flex items-center justify-between gap-3">
		<p class="text-sm text-muted">Plans bundle features. Clubs without a subscription use the default plan.</p>
		{#if canEdit}<button class="btn btn-primary btn-sm" onclick={create}><Plus size={14} aria-hidden="true" /> New plan</button>{/if}
	</div>
	{#if notice}<Alert kind="success">{notice}</Alert>{/if}
	{#if error && !deleting}<Alert kind="error">{error}</Alert>{/if}

	{#if !plans.length}
		<div class="card"><div class="card-body text-sm text-muted">No plans yet.{canEdit ? ' Create the first one.' : ''}</div></div>
	{:else}
		<div class="flex flex-col gap-4">
		{#each rows as row, r (r)}
		<div class="grid grid-cols-1 gap-4 md:grid-cols-[repeat(var(--n),minmax(0,1fr))]" style="--n: {row.length}">
			{#each row as p (p.id)}
				<div class="card flex flex-col {p.is_active ? 'opacity-100' : 'opacity-70'}">
					<div class="card-body flex-1 gap-3">
						<div>
							<div class="flex flex-wrap items-center gap-2">
								<span class="font-display text-xl leading-none font-extrabold text-ink uppercase">{p.name}</span>
								{#if p.is_default}<span class="badge badge-info">default</span>{/if}
								<span class="badge {p.is_active ? 'badge-success' : 'badge-ghost'}">{p.is_active ? 'active' : 'inactive'}</span>
							</div>
							<div class="mt-1 font-mono text-[11px] text-muted">{p.code} · {p.subscriber_count} {p.subscriber_count === 1 ? 'club' : 'clubs'}</div>
						</div>
						{#if p.description}<p class="line-clamp-2 text-sm text-slate" title={p.description}>{p.description}</p>{/if}
						<div>
							<div class="mb-1.5 font-mono text-[10px] tracking-[.08em] text-muted uppercase">{p.features.length} {p.features.length === 1 ? 'feature' : 'features'}</div>
							{#if p.features.length}
								<ul class="flex flex-wrap gap-1.5">
									{#each shownFeatures(p) as f (f.code)}
										<li class="badge badge-ghost gap-1 normal-case" title={f.name}>
											{f.name}{#if f.kind === 'limit'}<span class="font-mono text-ink">· {limitText(f)}</span>{/if}
										</li>
									{/each}
									{#if p.features.length > VISIBLE}
										<li>
											<button class="badge badge-info cursor-pointer normal-case" aria-expanded={!!expanded[p.id]} onclick={() => (expanded[p.id] = !expanded[p.id])}>
												{expanded[p.id] ? 'Show less' : `+${p.features.length - VISIBLE} more`}
											</button>
										</li>
									{/if}
								</ul>
							{:else}
								<p class="text-sm text-muted">No features</p>
							{/if}
						</div>
						{#if canEdit}
							<div class="mt-auto flex gap-2 border-t border-rule pt-3">
								<button class="btn btn-outline btn-sm" onclick={() => edit(p)}><Pencil size={13} aria-hidden="true" /> Edit</button>
								<button class="btn btn-outline btn-error btn-sm" onclick={() => ((deleting = p), (error = ''))} aria-label="Delete plan {p.name}"><Trash2 size={13} aria-hidden="true" /> Delete</button>
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>
		{/each}
		</div>
	{/if}
</div>

<PlanForm bind:open={formOpen} plan={editing} {catalog} onsaved={saved} />

<Modal open={!!deleting} title="Delete plan" onclose={() => (deleting = null)}>
	{#if deleting}
		<div class="flex flex-col gap-4">
			<p class="text-sm">Delete <strong>{deleting.name}</strong>? Only a plan that was never used can be deleted; otherwise deactivate it by editing it.</p>
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="flex justify-end gap-2">
				<button class="btn btn-ghost btn-sm" onclick={() => (deleting = null)}>Cancel</button>
				<button class="btn btn-error btn-sm" disabled={busy} onclick={remove}>Delete plan</button>
			</div>
		</div>
	{/if}
</Modal>
