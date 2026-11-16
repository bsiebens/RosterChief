<script lang="ts">
	import { Check } from '@lucide/svelte';
	import type { CatalogFeature, FullPlan } from '#lib/features.ts';

	let { plans, catalog }: { plans: FullPlan[]; catalog: CatalogFeature[] } = $props();
	const cell = (p: FullPlan, code: string) => p.features.find((f) => f.code === code);
</script>

{#if !plans.length || !catalog.length}
	<div class="card"><div class="card-body text-sm text-muted">Nothing to compare yet.</div></div>
{:else}
	<div class="card overflow-x-auto">
		<table class="table">
			<caption class="sr-only">Which features each plan includes</caption>
			<thead>
				<tr>
					<th scope="col" class="min-w-56">Feature</th>
					{#each plans as p (p.id)}<th scope="col" class="px-3.5 py-3 text-center">{p.name}{#if p.is_default}<span class="block font-mono text-[10px] normal-case">default</span>{/if}{#if !p.is_active}<span class="block font-mono text-[10px] normal-case">inactive</span>{/if}</th>{/each}
				</tr>
			</thead>
			<tbody>
				{#each catalog as f (f.code)}
					<tr>
						<th scope="row" class="min-w-56 px-3.5 py-3 text-left font-normal"><span class="font-semibold text-ink">{f.name}</span><span class="block font-mono text-[11px] text-muted">{f.code}</span></th>
						{#each plans as p (p.id)}
							{@const c = cell(p, f.code)}
							<td class="px-3.5 py-3 text-center">
								{#if !c}<span class="text-dim" aria-label="not included">—</span>
								{:else if c.kind === 'limit'}<span class="badge font-mono">{c.limit == null ? 'unlimited' : c.limit}</span>
								{:else}<Check size={16} class="mx-auto text-ok" aria-label="included" />{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
