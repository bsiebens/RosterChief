<script lang="ts">
	import { ChevronLeft, DoorOpen } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import MatchList from '#lib/components/tournaments/MatchList.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dateTimeLabel } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const d = $derived(data.team);
	const players = $derived(d.roster.filter((r) => r.role === 'player'));
	const staff = $derived(d.roster.filter((r) => r.role === 'staff'));
</script>

<svelte:head><title>{pageTitle(`${d.team.name} · ${d.tournament.name}`)}</title></svelte:head>

<a class="link inline-flex items-center gap-1 text-sm" href="/tournaments/{d.tournament.slug}/teams"><ChevronLeft size={14} aria-hidden="true" /> {t('tp.nav.teams')}</a>

<section class="card card-body flex-row items-center gap-4">
	<ClubCrest name={d.team.name} logoUrl={d.team.logo_url} size={64} bg={d.team.primary_color || undefined} />
	<div class="min-w-0">
		<h2 class="truncate font-display text-2xl font-extrabold text-ink">{d.team.name}</h2>
		{#if d.team.withdrawn}<span class="badge badge-error">{t('tp.team.withdrawn')}</span>{/if}
	</div>
</section>

{#if d.dressing_rooms.length}
	<section class="card card-body gap-2" aria-labelledby="tp-rooms">
		<h2 id="tp-rooms" class="flex items-center gap-2 font-display text-base font-bold text-ink"><DoorOpen size={16} aria-hidden="true" /> {t('tp.team.dressing')}</h2>
		<ul class="text-sm">
			{#each d.dressing_rooms as room (room.name + room.assigned_from)}
				<li>{room.name}{#if room.assigned_from} <span class="text-muted">· {dateTimeLabel(room.assigned_from)}{#if room.assigned_until} – {dateTimeLabel(room.assigned_until)}{/if}</span>{/if}</li>
			{/each}
		</ul>
	</section>
{/if}

<section class="card overflow-hidden" aria-labelledby="tp-matches">
	<h2 id="tp-matches" class="px-4 pt-4 pb-1 font-display text-base font-bold text-ink">{t('tp.team.matches')}</h2>
	{#if d.matches.length}
		<MatchList matches={d.matches} slug={d.tournament.slug} highlight={d.team.slug} />
	{:else}
		<p class="px-4 pb-4 text-sm text-muted">{t('tp.team.no_matches')}</p>
	{/if}
</section>

{#if d.roster.length}
	<section class="card overflow-hidden" aria-labelledby="tp-roster">
		<h2 id="tp-roster" class="px-4 pt-4 pb-2 font-display text-base font-bold text-ink">{t('tp.team.roster')}</h2>
		<table class="table">
			<thead><tr><th class="w-12">#</th><th>{t('tp.col.name')}</th><th>{t('tp.col.position')}</th></tr></thead>
			<tbody>
				{#each players as r, i (r.name + i)}<tr><td class="font-mono">{r.jersey_number ?? ''}</td><td>{r.name}</td><td class="text-muted">{r.position_label}</td></tr>{/each}
				{#each staff as r, i (r.name + i)}<tr><td></td><td>{r.name}</td><td class="text-muted">{r.position_label || t('tp.role.staff')}</td></tr>{/each}
			</tbody>
		</table>
	</section>
{/if}
