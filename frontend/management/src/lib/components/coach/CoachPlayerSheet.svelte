<script lang="ts">
	import { enhance } from '$app/forms';
	import { Pencil, Trash2 } from '@lucide/svelte';
	import { eventHref, formatEventDate, formatRate, kindLabel, rateTone, SPARK_STYLE, sparkLabel, statusLabel, statusTone, TONE, type MemberAttendance } from '#lib/attendance.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { RosterEntry } from '#lib/teams/server.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';

	/** A player's drawer: the attendance numbers (this team, plus the club-wide recent answers) and, for a team manager, edit and remove. */
	let { player, open, onclose, teamId, seasonId, canManage, onedit }: { player: RosterEntry | null; open: boolean; onclose: () => void; teamId: string; seasonId: string; canManage: boolean; onedit: () => void } = $props();

	let attendance = $state<MemberAttendance | null>(null);
	let loading = $state(false);
	let confirming = $state(false);
	let busy = $state(false);
	let error = $state('');
	const memberId = $derived(player?.member_id ?? '');
	const name = $derived(player ? `${player.first_name} ${player.last_name}` : '');
	const role = $derived(player?.is_captain ? t('coach.captain') : player?.is_alternate_captain ? t('coach.alternate') : '');
	const tiles = $derived(player ? ([['coach.rate_overall', player.attendance_rate], ['coach.rate_games', player.games_rate], ['coach.rate_practices', player.practices_rate]] as const) : []);
	const counts = $derived(attendance?.counts);
	const answered = $derived(counts ? counts.present + counts.absent : 0);

	$effect(() => {
		confirming = false;
		error = '';
		if (!open || !memberId) return;
		let live = true;
		attendance = null;
		loading = true;
		const params = seasonId ? `?season=${seasonId}` : '';
		fetch(`/api/v1/members/${memberId}/attendance${params}`, { headers: { accept: 'application/json' } })
			.then((res) => (res.ok ? res.json() : null))
			.catch(() => null)
			.then((data) => {
				if (live) {
					attendance = data;
					loading = false;
				}
			});
		return () => (live = false);
	});
</script>

<Sheet title={name || t('portal.squad')} {open} {onclose} wide>
	{#if player}
		<div class="grid min-w-0 gap-4 px-4 pb-5">
			<div class="flex min-w-0 items-center gap-3">
				<Avatar first_name={player.first_name} last_name={player.last_name} photoUrl={player.photo_url} size="size-14" text="text-lg" />
				<div class="min-w-0 flex-1">
					<h2 class="truncate font-display text-lg font-extrabold text-ink">{name}</h2>
					<p class="truncate text-sm text-muted">{[player.jersey_number !== null ? `#${player.jersey_number}` : '', player.position_name ?? '', role].filter(Boolean).join(' · ') || t('coach.no_position')}</p>
				</div>
			</div>

			<div class="grid grid-cols-3 gap-2" aria-label={t('coach.rate_overall')}>
				{#each tiles as [label, rate] (label)}
					{@const tone = TONE[rateTone(rate)]}
					<div class="min-w-0 rounded-box border px-2 py-3 text-center {tone.pill}">
						<p class="font-display text-2xl font-extrabold">{formatRate(rate)}</p>
						<p class="truncate text-xs">{t(label)}</p>
					</div>
				{/each}
			</div>
			<p class="-mt-2 text-xs text-muted">{t('coach.rate_help')}</p>

			{#if loading}
				<p class="py-3 text-center text-sm text-muted">{t('coach.att_loading')}</p>
			{:else if !attendance}
				<Alert kind="error">{t('attbox.failed')}</Alert>
			{:else if counts}
				<section class="grid gap-3">
					<p class="text-sm text-muted">{answered ? t('coach.answers_n', { n: answered }) : t('coach.no_answers')}</p>
					{#if answered}
						<div class="flex h-2.5 overflow-hidden rounded-full bg-rule" aria-hidden="true">
							<div class="{TONE.ok.bar} h-full" style="width: {(counts.present / answered) * 100}%"></div>
							<div class="{TONE.danger.bar} h-full" style="width: {(counts.absent / answered) * 100}%"></div>
						</div>
					{/if}
					<ul class="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
						{#each [['present', counts.present, TONE.ok.bar], ['absent', counts.absent, TONE.danger.bar], ['no_reply', counts.no_reply, 'bg-dim'], ['no_shows', counts.no_shows, TONE.danger.bar]] as [key, n, dot] (key)}
							<li class="flex items-center justify-between gap-2"><span class="inline-flex min-w-0 items-center gap-2 text-muted"><span class="size-2.5 shrink-0 rounded-full {dot}"></span><span class="truncate">{t(`attbox.${key}`)}</span></span><span class="font-mono text-xs font-semibold text-ink">{n}</span></li>
						{/each}
					</ul>
					{#if attendance.sparkline.length}
						<div class="grid grid-cols-6 gap-2">
							{#each attendance.sparkline as bar (bar.id)}
								{@const label = `${bar.title} · ${formatEventDate(bar.start)} · ${sparkLabel(bar.state)}`}
								<a class="h-8 rounded-md {SPARK_STYLE[bar.state]}" href={eventHref(bar.id)} title={label} aria-label={label}></a>
							{/each}
						</div>
					{/if}
				</section>
				<section>
					<h3 class="mb-1 font-mono text-[11px] tracking-[.12em] text-muted uppercase">{t('coach.recent_attendance')}</h3>
					<ul class="divide-y divide-rule rounded-box border border-line">
						{#each attendance.recent.slice(0, 6) as row (row.event.id)}
							<li class="flex min-h-12 items-center gap-3 px-3 py-2 {row.cancelled ? 'text-muted' : ''}">
								<span class="min-w-0 flex-1"><b class="block truncate text-sm text-ink {row.cancelled ? 'line-through' : ''}">{row.event.title}</b><span class="block truncate text-xs text-muted">{formatEventDate(row.event.start)} · {kindLabel(row.event.kind)}</span></span>
								<span class="inline-flex shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold {TONE[statusTone(row.status)].pill}">{statusLabel(row.status)}</span>
							</li>
						{:else}
							<li class="px-3 py-4 text-center text-sm text-muted">{t('coach.att_none')}</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if canManage}
				{#if error}<Alert kind="error">{error}</Alert>{/if}
				{#if confirming}
					<form
						method="POST"
						action="?tab=teams&team={teamId}&/removePlayer"
						class="grid gap-2 rounded-box border border-danger-border bg-danger-bg p-3"
						use:enhance={() => {
							busy = true;
							error = '';
							return async ({ result, update }) => {
								if (result.type === 'failure') error = String(result.data?.teamError ?? t('common.error.generic'));
								else {
									await update({ reset: false });
									if (result.type === 'success') onclose();
								}
								busy = false;
							};
						}}
					>
						<input type="hidden" name="team_id" value={teamId} />
						<input type="hidden" name="membership_id" value={player.id} />
						<p class="text-sm font-semibold text-danger">{t('coach.remove_confirm', { name })}</p>
						<div class="grid grid-cols-2 gap-2">
							<button class="btn btn-outline min-h-11" type="button" onclick={() => (confirming = false)}>{t('common.cancel')}</button>
							<button class="btn btn-error min-h-11" type="submit" disabled={busy}>{t('coach.remove_yes')}</button>
						</div>
					</form>
				{:else}
					<div class="grid grid-cols-2 gap-2">
						<button class="btn btn-outline min-h-11 gap-2" type="button" onclick={onedit}><Pencil size={16} aria-hidden="true" />{t('common.edit')}</button>
						<button class="btn btn-outline btn-error min-h-11 gap-2" type="button" onclick={() => (confirming = true)}><Trash2 size={16} aria-hidden="true" />{t('coach.remove_player')}</button>
					</div>
				{/if}
			{/if}
		</div>
	{/if}
</Sheet>
