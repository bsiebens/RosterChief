<script lang="ts">
	import { Shield } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { seasonName } from '#lib/members/detail-helpers.ts';
	import type { MemberDetail, Season } from '#lib/members/detail-types.ts';

	let { member, seasons }: { member: MemberDetail; seasons: Season[] } = $props();
	const empty = $derived(!member.team_memberships.length && !member.staff_assignments.length);
</script>

<div class="card card-body">
	<h2 class="card-title"><Shield size={18} /> {t('members.detail.teams')}</h2>
	{#if empty}
		<p class="text-sm text-muted">{t('members.detail.no_teams')}</p>
	{:else}
		<ul class="divide-y divide-rule">
			{#each member.team_memberships as tm (tm.id)}
				<li class="flex flex-wrap items-center justify-between gap-2 py-2">
					<a class="link font-semibold" href="/teams/{tm.team_id}?season={tm.season_id}">{tm.team_name}</a>
					<span class="flex items-center gap-2 text-xs leading-none text-muted">
						<span class="badge badge-sm badge-neutral">{tm.position ?? t('members.detail.player')}</span>
						{#if tm.jersey_number != null}<span class="inline-flex items-center font-mono leading-none">{t('members.detail.jersey', { n: tm.jersey_number })}</span>{/if}
						<span class="inline-flex items-center leading-none">{seasonName(seasons, tm.season_id)}</span>
					</span>
				</li>
			{/each}
			{#each member.staff_assignments as sa (sa.id)}
				<li class="flex flex-wrap items-center justify-between gap-2 py-2">
					<a class="link font-semibold" href="/teams/{sa.team_id}?season={sa.season_id}">{sa.team_name}</a>
					<span class="flex items-center gap-2 text-xs leading-none text-muted">
						<span class="badge badge-sm badge-info">{sa.position}</span>
						<span class="inline-flex items-center leading-none">{seasonName(seasons, sa.season_id)}</span>
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>
