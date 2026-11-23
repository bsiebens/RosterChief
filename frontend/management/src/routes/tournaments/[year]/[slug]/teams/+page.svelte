<script lang="ts">
	import { DoorOpen } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.teams.tournament);
</script>

<svelte:head><title>{pageTitle(`${tn.name} · ${t('tp.nav.teams')}`)}</title></svelte:head>

{#if !data.teams.teams.length}
	<div class="rounded-2xl border border-line bg-white py-12 text-center text-muted" role="status">{t('tp.teams.empty')}</div>
{:else}
	<p class="font-display text-sm font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.team_count', { n: data.teams.teams.length })}</p>
	<ul class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
		{#each data.teams.teams as team (team.id)}
			<li>
				<a class="group flex h-full flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md" href="/tournaments/{tn.path}/teams/{team.slug}">
					<span class="h-2" style:background={team.primary_color || 'var(--color-club)'}></span>
					<span class="flex flex-1 flex-col items-center gap-3 px-4 pt-6 pb-5 text-center">
						<ClubCrest name={team.name} logoUrl={team.logo_url} size={88} bg={team.primary_color || undefined} />
						<b class="font-display text-lg leading-tight font-extrabold text-ink group-hover:underline">{team.name}</b>
						{#if team.dressing_rooms.length}<span class="flex items-center gap-1 font-mono text-xs text-muted"><DoorOpen size={12} aria-hidden="true" /> {team.dressing_rooms.join(', ')}</span>{/if}
					</span>
				</a>
			</li>
		{/each}
	</ul>
{/if}
