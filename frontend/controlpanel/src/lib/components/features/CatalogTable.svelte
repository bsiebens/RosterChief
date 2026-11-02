<script lang="ts">
	import type { CatalogFeature } from '#lib/features.ts';
	let { catalog }: { catalog: CatalogFeature[] } = $props();
</script>

<div class="card">
	<div class="card-body gap-1 pb-3">
		<div class="card-title">Feature catalog</div>
		<p class="text-sm text-muted">Features are defined in code and cannot be edited here. Plans and per-club overrides decide who gets them.</p>
	</div>
	<div class="overflow-x-auto">
		<table class="table">
			<thead><tr><th>Feature</th><th>Kind</th><th>Limit scope</th><th>Status</th><th>Description</th></tr></thead>
			<tbody>
				{#each catalog as f (f.code)}
					<tr>
						<td><div class="font-semibold text-ink">{f.name}</div><div class="font-mono text-[11px] text-muted">{f.code}</div></td>
						<td><span class="badge">{f.kind}</span></td>
						<td class="font-mono text-xs">{f.limit_scope ?? '—'}</td>
						<td><span class="badge {f.is_active ? 'badge-success' : 'badge-warning'}">{f.is_active ? 'active' : 'not synced'}</span></td>
						<td class="max-w-80 text-sm text-muted">{f.description || '—'}</td>
					</tr>
				{:else}
					<tr><td colspan="5" class="text-sm text-muted">The catalog is empty.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>
