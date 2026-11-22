<script lang="ts">
	import { ChevronRight, DoorOpen } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.teams.tournament);
</script>

<svelte:head><title>{pageTitle(`${tn.name} · ${t('tp.nav.teams')}`)}</title></svelte:head>

{#if !data.teams.teams.length}
	<div class="card card-body items-center py-8 text-center text-muted" role="status">{t('tp.teams.empty')}</div>
{:else}
	<ul class="card divide-y divide-rule overflow-hidden">
		{#each data.teams.teams as team (team.id)}
			<li>
				<a class="flex min-h-14 items-center gap-3 px-4 py-2 hover:bg-subhead" href="/tournaments/{tn.path}/teams/{team.slug}">
					<ClubCrest name={team.name} logoUrl={team.logo_url} size={36} bg={team.primary_color || undefined} />
					<span class="min-w-0 flex-1">
						<b class="block truncate text-ink">{team.name}</b>
						{#if team.dressing_rooms.length}<span class="flex items-center gap-1 text-xs text-muted"><DoorOpen size={12} aria-hidden="true" /> {team.dressing_rooms.join(', ')}</span>{/if}
					</span>
					<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
				</a>
			</li>
		{/each}
	</ul>
{/if}
