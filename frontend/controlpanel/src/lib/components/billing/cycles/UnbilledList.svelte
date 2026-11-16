<script lang="ts">
	import { ExternalLink } from '@lucide/svelte';
	import type { Unbilled } from './types.ts';

	let { clubs, canAct, onstart }: { clubs: Unbilled[]; canAct: boolean; onstart: (club: Unbilled) => void } = $props();
</script>

<div class="card overflow-x-auto">
	<table class="table">
		<thead>
			<tr><th>Club</th><th>Current plan</th><th class="text-right">Actions</th></tr>
		</thead>
		<tbody>
			{#each clubs as c (c.id)}
				<tr>
					<td>
						<a class="link font-semibold text-ink" href="/clubs/{c.id}">{c.name}</a>
						<div class="font-mono text-[11px] text-muted">{c.slug}</div>
					</td>
					<td class="text-sm">{c.plan_code ?? '—'} <span class="font-mono text-[11px] text-muted">(free of charge)</span></td>
					<td>
						<div class="flex flex-wrap items-center justify-end gap-2">
							{#if canAct}
								<button class="btn btn-primary btn-sm" onclick={() => onstart(c)}>Start billing<span class="sr-only"> for {c.name}</span></button>
							{/if}
							<a class="btn btn-outline btn-sm btn-square" href="/clubs/{c.id}" aria-label="Open club {c.name}" title="Open club"><ExternalLink size={13} aria-hidden="true" /></a>
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
