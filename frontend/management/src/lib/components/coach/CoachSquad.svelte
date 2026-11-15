<script lang="ts">
	import { Search } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { TeamDetail } from '#lib/teams/server.ts';
	import Avatar from '#lib/components/Avatar.svelte';

	type Player = TeamDetail['roster'][number];
	let { team }: { team: TeamDetail | null } = $props();
	let query = $state('');
	const roster = $derived(((team?.roster ?? []) as Player[]).filter((player) => !query.trim() || `${player.last_name} ${player.first_name} ${player.first_name} ${player.last_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
	const groups = $derived.by(() => {
		const map = new Map<string, Player[]>();
		for (const player of roster) {
			const key = player.position_name ?? '';
			map.set(key, [...(map.get(key) ?? []), player]);
		}
		return [...map.entries()];
	});
	const sortByName = (a: Player, b: Player) => `${a.last_name} ${a.first_name}`.localeCompare(`${b.last_name} ${b.first_name}`);
	const tone = (rate: number) => (rate >= 80 ? 'badge-success' : rate >= 60 ? 'badge-warning' : 'badge-error');
	const role = (player: Player) => (player.is_captain ? t('coach.captain') : player.is_alternate_captain ? t('coach.alternate') : '');
</script>

{#if team}
	<div class="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
		<div class="grid min-w-0 gap-3">
			<label class="relative block"><span class="sr-only">{t('coach.search_squad')}</span><Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" /><input class="input w-full pl-9" type="search" placeholder={t('coach.search_squad')} bind:value={query} /></label>
			{#each groups as [position, players] (position)}
				<h2 class="mt-1 font-mono text-[11px] tracking-[.12em] text-muted uppercase">{position || t('coach.no_position')} · {players.length}</h2>
				<section class="card divide-y divide-rule">
					{#each [...players].sort(sortByName) as player (player.id)}
						<div class="flex min-h-14 items-center gap-3 px-3.5 py-2">
							<Avatar first_name={player.first_name} last_name={player.last_name} photoUrl={player.photo_url} size="size-10" text="text-sm" />
							<span class="min-w-0 flex-1">
								<b class="block truncate text-sm text-ink">{player.last_name}, {player.first_name}</b>
								<span class="block text-xs text-muted">{#if player.jersey_number !== null}#{player.jersey_number}{/if}{#if role(player)}{player.jersey_number !== null ? ' · ' : ''}{role(player)}{/if}</span>
							</span>
							{#if player.attendance_rate != null}<span class="badge {tone(player.attendance_rate)}" title={t('coach.attendance_pct', { n: player.attendance_rate })}>{player.attendance_rate}%</span>{/if}
						</div>
					{/each}
				</section>
			{:else}
				<p class="card card-body text-sm text-muted">{t(query.trim() ? 'coach.no_match' : 'portal.no_players')}</p>
			{/each}
		</div>
		<section class="card">
			<div class="card-h"><h2>{t('portal.staff')}</h2></div>
			<ul class="divide-y divide-rule">
				{#each team.staff as member (member.id)}
					<li class="px-4 py-2"><p class="text-sm font-semibold text-ink">{member.first_name} {member.last_name}</p>{#if member.position_name}<p class="text-xs text-muted">{member.position_name}</p>{/if}</li>
				{:else}
					<li class="px-4 py-3 text-sm text-muted">{t('portal.no_staff')}</li>
				{/each}
			</ul>
		</section>
	</div>
{:else}
	<p class="card card-body text-sm text-muted">{t('portal.no_teams')}</p>
{/if}
