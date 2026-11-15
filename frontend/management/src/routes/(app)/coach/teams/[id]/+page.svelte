<script lang="ts">
	import { ArrowLeft, ChevronRight, Users, UsersRound } from '@lucide/svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: data.team.name }));
</script>

<svelte:head><title>{pageTitle(data.team.name)}</title></svelte:head>

<div class="grid w-full min-w-0 gap-4">
	<div class="flex flex-wrap gap-2">
		<a class="btn btn-outline gap-2" href="/coach?tab=teams"><ArrowLeft size={16} aria-hidden="true" />{t('portal.teams')}</a>
		<a class="btn btn-outline hidden gap-2 xl:pointer-fine:inline-flex" href="/teams/{data.team.id}">{t('portal.team_management')}<ChevronRight size={16} aria-hidden="true" /></a>
	</div>
	<div class="grid gap-5 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] md:items-start">
		<section class="card card-body gap-3">
			<h2 class="card-title"><Users size={16} aria-hidden="true" />{t('portal.roster')}</h2>
			<ul class="divide-y divide-rule">
				{#each data.team.roster as player (player.id)}
					<li class="flex min-h-12 items-center gap-3 py-2">
						{#if player.jersey_number !== null}<span class="grid size-8 shrink-0 place-items-center rounded-box bg-surface font-mono text-sm font-semibold">{player.jersey_number}</span>{/if}
						<div class="min-w-0">
							<p class="font-semibold">{player.first_name} {player.last_name}</p>
							{#if player.position_name}<p class="text-xs text-muted">{player.position_name}</p>{/if}
						</div>
					</li>
				{:else}
					<li class="text-sm text-muted">{t('portal.no_players')}</li>
				{/each}
			</ul>
		</section>
		<section class="card card-body gap-3">
			<h2 class="card-title"><UsersRound size={16} aria-hidden="true" />{t('portal.staff')}</h2>
			<ul class="divide-y divide-rule">
				{#each data.team.staff as member (member.id)}
					<li class="py-2">
						<p class="font-semibold">{member.first_name} {member.last_name}</p>
						{#if member.position_name}<p class="text-xs text-muted">{member.position_name}</p>{/if}
					</li>
				{:else}
					<li class="text-sm text-muted">{t('portal.no_staff')}</li>
				{/each}
			</ul>
		</section>
	</div>
</div>
