<script lang="ts">
	import { Building2 } from '@lucide/svelte';
	import type { UserDetail } from '#lib/users.ts';

	let { clubs }: { clubs: UserDetail['clubs'] } = $props();
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title"><Building2 size={16} aria-hidden="true" /> Clubs <span class="badge badge-ghost ml-1">{clubs.length}</span></div>
		{#if clubs.length}
			<div class="overflow-x-auto">
				<table class="table">
					<thead><tr><th>Club</th><th>Roles</th><th>Access</th></tr></thead>
					<tbody>
						{#each clubs as c (c.club.id)}
							<tr>
								<td>
									<a class="font-semibold text-ink hover:underline" href="/clubs/{c.club.id}">{c.club.name}</a>
									{#if c.club.archived}<span class="badge badge-warning badge-sm ml-1">archived</span>{/if}
									<span class="block font-mono text-[11px] text-muted">{c.club.slug}</span>
								</td>
								<td>
									<span class="flex flex-wrap gap-1">{#each c.roles as r (r)}<span class="badge badge-ghost badge-sm">{r}</span>{:else}<span class="text-dim">—</span>{/each}</span>
								</td>
								<td>
									<span class="flex flex-wrap gap-1">
										{#if c.is_owner}<span class="badge badge-info badge-sm">owner</span>{/if}
										{#if c.is_admin}<span class="badge badge-neutral badge-sm">admin</span>{/if}
										{#if !c.is_owner && !c.is_admin}<span class="text-dim">—</span>{/if}
									</span>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="text-sm text-muted">This account is not a member of any club.</p>
		{/if}
	</div>
</div>
