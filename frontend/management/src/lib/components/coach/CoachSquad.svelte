<script lang="ts">
	import { enhance } from '$app/forms';
	import { Pencil, Plus, Search, Trash2 } from '@lucide/svelte';
	import { formatRate, rateTone, TONE } from '#lib/attendance.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Position, StaffEntry, TeamDetail } from '#lib/teams/server.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import CoachPlayerSheet from '#lib/components/coach/CoachPlayerSheet.svelte';
	import CoachSquadForm from '#lib/components/coach/CoachSquadForm.svelte';

	type Player = TeamDetail['roster'][number];
	let { team, positions = [] }: { team: TeamDetail | null; positions?: Position[] } = $props();
	let query = $state('');
	// Which sheet is open: the player drawer, the player form (add / edit) or the staff form (add / edit).
	let viewing = $state('');
	let sheet = $state<'' | 'player' | 'staff'>('');
	let editing = $state<{ player: Player | null; staff: StaffEntry | null }>({ player: null, staff: null });
	let removing = $state('');
	let staffBusy = $state(false);
	let staffError = $state('');

	const canManage = $derived(team?.can_manage ?? false);
	const roster = $derived(((team?.roster ?? []) as Player[]).filter((player) => !query.trim() || `${player.last_name} ${player.first_name} ${player.first_name} ${player.last_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
	const groups = $derived.by(() => {
		const map = new Map<string, Player[]>();
		for (const player of roster) {
			const key = player.position_name ?? '';
			map.set(key, [...(map.get(key) ?? []), player]);
		}
		return [...map.entries()];
	});
	const viewed = $derived(team?.roster.find((player) => player.id === viewing) ?? null);
	const sortByName = (a: Player, b: Player) => `${a.last_name} ${a.first_name}`.localeCompare(`${b.last_name} ${b.first_name}`);
	const role = (player: Player) => (player.is_captain ? t('coach.captain') : player.is_alternate_captain ? t('coach.alternate') : '');
	const pill = (rate: number | null | undefined) => `inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold ${TONE[rateTone(rate)].pill}`;
	const LEGEND = [['ok', 'coach.legend_good'], ['warn', 'coach.legend_mid'], ['danger', 'coach.legend_low'], ['muted', 'coach.legend_none']] as const;

	function openPlayer(player: Player | null) {
		viewing = '';
		editing = { player, staff: null };
		sheet = 'player';
	}
	function openStaff(staff: StaffEntry | null) {
		editing = { player: null, staff };
		sheet = 'staff';
	}
</script>

{#if team}
	<div class="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
		<div class="grid min-w-0 gap-3">
			<div class="flex min-w-0 items-center gap-2">
				<label class="relative block min-w-0 flex-1"><span class="sr-only">{t('coach.search_squad')}</span><Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" /><input class="input w-full pl-9" type="search" placeholder={t('coach.search_squad')} bind:value={query} /></label>
				{#if canManage}<button type="button" class="btn btn-primary btn-sm min-h-10 shrink-0 gap-1.5" onclick={() => openPlayer(null)}><Plus size={16} aria-hidden="true" />{t('coach.add_player')}</button>{/if}
			</div>
			<div class="grid gap-1 text-xs text-muted">
				<ul class="flex flex-wrap gap-x-3 gap-y-1" aria-label={t('coach.rate_overall')}>
					{#each LEGEND as [tone, key] (key)}<li class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-full {TONE[tone].bar}"></span>{t(key)}</li>{/each}
				</ul>
				<p>{t('coach.rate_help')}</p>
			</div>
			{#each groups as [position, players] (position)}
				<h2 class="mt-1 font-mono text-[11px] tracking-[.12em] text-muted uppercase">{position || t('coach.no_position')} · {players.length}</h2>
				<section class="card divide-y divide-rule">
					{#each [...players].sort(sortByName) as player (player.id)}
						<button type="button" class="flex min-h-14 w-full min-w-0 items-center gap-3 px-3.5 py-2 text-left hover:bg-subhead" onclick={() => (viewing = player.id)}>
							<Avatar first_name={player.first_name} last_name={player.last_name} photoUrl={player.photo_url} size="size-10" text="text-sm" />
							<span class="min-w-0 flex-1">
								<b class="block truncate text-sm text-ink">{player.last_name}, {player.first_name}</b>
								<span class="block truncate text-xs text-muted">{#if player.jersey_number !== null}#{player.jersey_number}{/if}{#if role(player)}{player.jersey_number !== null ? ' · ' : ''}{role(player)}{/if}</span>
							</span>
							<span class="flex shrink-0 flex-col items-end gap-1" title={t('coach.attendance_pct', { n: player.attendance_rate ?? 0 })}>
								<span class={pill(player.attendance_rate)}>{formatRate(player.attendance_rate)}</span>
								<span class="flex gap-1 font-mono text-[10px] text-muted">
									<span class="rounded px-1 {TONE[rateTone(player.games_rate)].pill} border">{t('coach.rate_g')} {formatRate(player.games_rate)}</span>
									<span class="rounded px-1 {TONE[rateTone(player.practices_rate)].pill} border">{t('coach.rate_p')} {formatRate(player.practices_rate)}</span>
								</span>
							</span>
						</button>
					{/each}
				</section>
			{:else}
				<p class="card card-body text-sm text-muted">{t(query.trim() ? 'coach.no_match' : 'portal.no_players')}</p>
			{/each}
		</div>
		<section class="card min-w-0">
			<div class="card-h"><h2>{t('portal.staff')}</h2>{#if canManage}<button type="button" class="btn btn-outline btn-sm ml-auto min-h-9 gap-1.5" onclick={() => openStaff(null)}><Plus size={14} aria-hidden="true" />{t('coach.add_staff')}</button>{/if}</div>
			{#if staffError}<div class="px-4 pt-3"><Alert kind="error">{staffError}</Alert></div>{/if}
			<ul class="divide-y divide-rule">
				{#each team.staff as member (member.id)}
					<li class="min-w-0 px-4 py-2">
						<div class="flex min-h-11 min-w-0 items-center gap-3">
							<Avatar first_name={member.first_name} last_name={member.last_name} photoUrl={member.photo_url} size="size-9" text="text-xs" />
							<span class="min-w-0 flex-1">
								<b class="block truncate text-sm text-ink">{member.first_name} {member.last_name}{#if member.is_self} <span class="badge badge-ghost badge-sm align-middle">{t('coach.you')}</span>{/if}</b>
								{#if member.position_name}<span class="block truncate text-xs text-muted">{member.position_name}</span>{/if}
							</span>
							{#if canManage && !member.is_self}
								<button type="button" class="btn btn-ghost btn-square btn-sm" aria-label={t('teams.staff.edit_named', { name: `${member.first_name} ${member.last_name}` })} onclick={() => openStaff(member)}><Pencil size={16} aria-hidden="true" /></button>
								<button type="button" class="btn btn-ghost btn-square btn-sm text-danger" aria-label={t('teams.staff.remove_named', { name: `${member.first_name} ${member.last_name}` })} onclick={() => ((removing = removing === member.id ? '' : member.id), (staffError = ''))}><Trash2 size={16} aria-hidden="true" /></button>
							{/if}
						</div>
						{#if removing === member.id}
							<form
								method="POST"
								action="?tab=teams&team={team.id}&/removeStaff"
								class="mt-2 grid gap-2 rounded-box border border-danger-border bg-danger-bg p-3"
								use:enhance={() => {
									staffBusy = true;
									staffError = '';
									return async ({ result, update }) => {
										if (result.type === 'failure') staffError = String(result.data?.teamError ?? t('common.error.generic'));
										else await update({ reset: false });
										if (result.type !== 'failure') removing = '';
										staffBusy = false;
									};
								}}
							>
								<input type="hidden" name="team_id" value={team.id} />
								<input type="hidden" name="assignment_id" value={member.id} />
								<p class="text-sm font-semibold text-danger">{t('coach.remove_confirm_staff', { name: `${member.first_name} ${member.last_name}` })}</p>
								<div class="grid grid-cols-2 gap-2">
									<button class="btn btn-outline min-h-11" type="button" onclick={() => (removing = '')}>{t('common.cancel')}</button>
									<button class="btn btn-error min-h-11" type="submit" disabled={staffBusy}>{t('coach.remove_yes')}</button>
								</div>
							</form>
						{/if}
					</li>
				{:else}
					<li class="px-4 py-3 text-sm text-muted">{t('portal.no_staff')}</li>
				{/each}
			</ul>
			{#if canManage && team.staff.some((member) => member.is_self)}<p class="border-t border-rule px-4 py-2 text-xs text-muted">{t('coach.own_entry')}</p>{/if}
			{#if !canManage}<p class="border-t border-rule px-4 py-2 text-xs text-muted">{t('coach.not_a_manager')}</p>{/if}
		</section>
	</div>

	<CoachPlayerSheet player={viewed} open={!!viewed} onclose={() => (viewing = '')} teamId={team.id} seasonId={team.season_id} {canManage} onedit={() => openPlayer(viewed)} />
	<CoachSquadForm
		mode={sheet === 'staff' ? 'staff' : 'player'}
		open={sheet !== ''}
		onclose={() => (sheet = '')}
		teamId={team.id}
		seasonId={team.season_id}
		{positions}
		exclude={sheet === 'staff' ? team.staff.map((member) => member.member_id) : team.roster.map((player) => player.member_id)}
		player={editing.player}
		staff={editing.staff}
	/>
{:else}
	<p class="card card-body text-sm text-muted">{t('portal.no_teams')}</p>
{/if}
