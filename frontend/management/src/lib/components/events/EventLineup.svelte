<script lang="ts">
	import { CalendarClock, Megaphone, Save, Trash2, Users, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api, type ApiCall } from '#lib/management-api.ts';
	import { formatDateTime, statusLabelKey, statusStyle, toLocalInput, type Lineup, type LineupMember, type LineupPublishOut } from '#lib/events/types.ts';

	/** The Line-up tab (games and tournaments): pick, save, publish or schedule; a read-only list for everyone else. */
	let { eventId, initial, cancelled }: { eventId: string; initial: Lineup; cancelled: boolean } = $props();

	// svelte-ignore state_referenced_locally
	let lineup = $state<Lineup>($state.snapshot(initial));
	// svelte-ignore state_referenced_locally
	let picked = $state<string[]>(initial.members.filter((m) => m.selected).map((m) => m.member.id));
	$effect(() => {
		lineup = $state.snapshot(initial);
		picked = initial.members.filter((m) => m.selected).map((m) => m.member.id);
	});

	const manage = $derived(lineup.can_manage && !cancelled);
	const saved = $derived(lineup.members.filter((m) => m.selected).map((m) => m.member.id));
	const dirty = $derived(picked.length !== saved.length || picked.some((id) => !saved.includes(id)));
	let teamId = $state('');
	let when = $state('');
	let busy = $state(false);
	let errorMsg = $state('');
	let okMsg = $state('');

	const STATUS_CHIP: Record<string, string> = {
		none: 'bg-subhead text-muted border-line',
		draft: 'bg-warn-bg text-warn-text border-warn-border',
		scheduled: 'bg-info-bg text-info-text border-info-border',
		published: 'bg-ok-bg text-ok-text border-ok-border'
	};
	const failText = (res: ApiCall<unknown>) => res.fields.__all__ || res.fields.when || res.fields.team_id || res.message || t('common.error.generic');

	/** Members grouped by position (roster order kept). */
	function byPosition(members: LineupMember[]) {
		const groups = new Map<string, LineupMember[]>();
		for (const m of members) {
			const k = m.position || t('events.lineup.no_position');
			groups.set(k, [...(groups.get(k) ?? []), m]);
		}
		return [...groups.entries()];
	}
	const groups = $derived(byPosition(lineup.members));
	/** Takes one player out of the pick (saved with "Save draft" or "Publish"). */
	const drop = (id: string) => (picked = picked.filter((x) => x !== id));
	const pickedMembers = $derived(lineup.members.filter((m) => picked.includes(m.member.id)));
	/** The pick grouped by position (the same order as the Players card), jersey number within a position. */
	const pickedGroups = $derived(
		groups
			.map(([position, members]) => [position, members.filter((m) => picked.includes(m.member.id)).sort((a, b) => (a.jersey_number ?? 999) - (b.jersey_number ?? 999))] as [string, LineupMember[]])
			.filter(([, members]) => members.length)
	);

	async function call<T>(fn: () => Promise<ApiCall<T>>, done?: (data: T) => void) {
		busy = true;
		errorMsg = '';
		okMsg = '';
		const res = await fn();
		busy = false;
		if (res.ok && res.data) done?.(res.data);
		else errorMsg = failText(res);
		return res.ok;
	}
	const apply = (l: Lineup) => {
		lineup = l;
		picked = l.members.filter((m) => m.selected).map((m) => m.member.id);
	};
	const save = () => call(() => api<Lineup>('PUT', `/events/${eventId}/lineup`, { body: { team_id: lineup.team?.id ?? (teamId || null), member_ids: picked } }), (l) => (apply(l), (okMsg = t('events.lineup.saved'))));
	async function publish() {
		if (dirty && !(await save())) return;
		await call(
			() => api<LineupPublishOut>('POST', `/events/${eventId}/lineup/publish`),
			(r) => {
				apply(r.lineup);
				okMsg = t('events.lineup.published_msg', { in: r.newly_selected.length, out: r.dropped.length });
			}
		);
	}
	async function schedule(e: SubmitEvent) {
		e.preventDefault();
		if (!when) return;
		if (dirty && !(await save())) return;
		await call(() => api<Lineup>('POST', `/events/${eventId}/lineup/schedule`, { body: { when: new Date(when).toISOString() } }), (l) => (apply(l), (okMsg = t('events.lineup.scheduled_msg'))));
	}
	const unschedule = () => call(() => api<Lineup>('DELETE', `/events/${eventId}/lineup/schedule`), apply);
	const chooseTeam = () => teamId && call(() => api<Lineup>('PUT', `/events/${eventId}/lineup`, { body: { team_id: teamId, member_ids: [] } }), apply);
</script>

<div class="grid gap-4">
	<!-- Managers see the status on their Players card; everyone else gets it here. -->
	<div class="flex flex-wrap items-center gap-3 {manage && lineup.team ? 'hidden' : ''}">
		<span class="badge border {STATUS_CHIP[lineup.status] ?? STATUS_CHIP.none}">{t(`events.lineup.status.${lineup.status}`)}</span>
		{#if lineup.published_at}<span class="font-mono text-xs text-muted">{t('events.lineup.published_at', { when: formatDateTime(lineup.published_at) })}</span>{/if}
		{#if lineup.scheduled_publish_at && !lineup.published_at}<span class="font-mono text-xs text-info-text">{t('events.lineup.scheduled_at', { when: formatDateTime(lineup.scheduled_publish_at) })}</span>{/if}
	</div>
	{#if errorMsg}<Alert kind="error">{errorMsg}</Alert>{/if}
	{#if okMsg}<Alert kind="success">{okMsg}</Alert>{/if}

	{#if manage}
		{#if !lineup.team}
			<div class="card card-body gap-3">
				<p class="text-sm">{lineup.teams.length ? t('events.lineup.pick_team') : t('events.lineup.no_team')}</p>
				{#if lineup.teams.length}
					<div class="flex flex-wrap items-center gap-2">
						<select class="select select-sm w-auto" aria-label={t('events.lineup.team')} bind:value={teamId}>
							<option value="">—</option>
							{#each lineup.teams as tm (tm.id)}<option value={tm.id}>{tm.name}</option>{/each}
						</select>
						<button class="btn btn-primary btn-sm" type="button" disabled={!teamId || busy} onclick={chooseTeam}>{t('events.lineup.use_team')}</button>
					</div>
				{/if}
			</div>
		{:else}
			<div class="grid gap-4 lg:grid-cols-2">
				<div class="card">
					<div class="flex items-start justify-between gap-3 border-b border-line px-5 py-3">
						<div>
							<h3 class="font-display text-base font-extrabold tracking-[.04em] text-ink uppercase flex items-center gap-2"><Users size={16} class="shrink-0" aria-hidden="true" /> {t('events.lineup.players')}</h3>
							<p class="text-xs text-muted">{t('events.lineup.players_help')}</p>
						</div>
						<div class="flex shrink-0 flex-col items-end gap-1">
							<span class="badge border {STATUS_CHIP[lineup.status] ?? STATUS_CHIP.none}">{t(`events.lineup.status.${lineup.status}`)}</span>
							{#if lineup.published_at}<span class="font-mono text-[11px] text-muted">{t('events.lineup.published_at', { when: formatDateTime(lineup.published_at) })}</span>{:else if lineup.scheduled_publish_at}<span class="font-mono text-[11px] text-info-text">{t('events.lineup.scheduled_at', { when: formatDateTime(lineup.scheduled_publish_at) })}</span>{/if}
						</div>
					</div>
					{#each groups as [position, members] (position)}
						<fieldset class="border-b border-rule px-5 py-3 last:border-b-0">
							<legend class="sr-only">{position}</legend>
							<p class="mb-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase" aria-hidden="true">{position}</p>
							<ul class="grid gap-1 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
								{#each members as m (m.member.id)}
									<li>
										<label class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-paper {m.available ? '' : 'cursor-not-allowed opacity-50'}">
											<input type="checkbox" class="checkbox" bind:group={picked} value={m.member.id} disabled={!m.available && !picked.includes(m.member.id)} />
											<span class="w-7 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
											<span class="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{m.member.name}</span>
											{#if !m.on_roster}<span class="badge badge-xs badge-outline" title={t('events.lineup.not_on_roster')}>{t('events.lineup.guest')}</span>{/if}
											<span class="badge badge-xs border {statusStyle(m.status)}">{t(statusLabelKey(m.status))}</span>
										</label>
									</li>
								{/each}
							</ul>
						</fieldset>
					{:else}
						<p class="px-5 py-6 text-center text-sm text-muted">{t('events.lineup.no_players')}</p>
					{/each}
				</div>

				<div class="grid content-start gap-4">
					<div class="card card-body gap-2">
						<div class="flex items-center justify-between gap-2">
							<h3 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('events.lineup.picked', { n: picked.length })}</h3>
							{#if dirty}<span class="badge badge-sm border border-warn-border bg-warn-bg text-warn-text">{t('events.lineup.unsaved').replace(/\.$/, '')}</span>{/if}
						</div>
						<ul class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
							{#each pickedGroups as [position, members] (position)}
								<li class="col-span-2 mt-1 font-display text-[11px] font-bold tracking-[.08em] text-muted uppercase first:mt-0" aria-hidden="true">{position}</li>
								{#each members as m (m.member.id)}
									<li class="group flex items-center gap-2">
										<span class="w-7 shrink-0 text-right font-display font-extrabold">{m.jersey_number ?? ''}</span>
										<span class="min-w-0 flex-1 truncate">{m.member.name}</span>
										<button type="button" class="btn btn-ghost btn-sm btn-square shrink-0 text-muted hover:bg-danger-bg hover:text-danger" disabled={busy} aria-label={t('events.lineup.drop_named', { name: m.member.name })} title={t('events.lineup.drop')} onclick={() => drop(m.member.id)}><Trash2 size={17} aria-hidden="true" /></button>
									</li>
								{/each}
							{:else}<li class="col-span-2 text-muted">{t('events.lineup.none_picked')}</li>{/each}
						</ul>
						<div class="mt-2 flex flex-wrap gap-2">
							<button class="btn btn-outline btn-sm gap-2" type="button" disabled={!dirty || busy} onclick={save}><Save size={14} /> {t('events.lineup.save')}</button>
							<button class="btn btn-primary btn-sm gap-2" type="button" disabled={busy || (!picked.length && !lineup.published_at)} onclick={publish}><Megaphone size={14} /> {lineup.published_at ? t('events.lineup.publish_changes') : t('events.lineup.publish')}</button>
						</div>
						<p class="text-xs text-muted">{lineup.published_at ? t('events.lineup.notify_hint_published') : t('events.lineup.notify_hint')}</p>
					</div>
					{#if !lineup.published_at}
						<div class="card card-body gap-2">
							<h3 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><CalendarClock size={16} aria-hidden="true" /> {t('events.lineup.schedule')}</h3>
							{#if lineup.scheduled_publish_at}
								<p class="text-sm">{t('events.lineup.scheduled_at', { when: formatDateTime(lineup.scheduled_publish_at) })}</p>
								<button class="btn btn-outline btn-sm gap-2 self-start" type="button" disabled={busy} onclick={unschedule}><X size={14} /> {t('events.lineup.unschedule')}</button>
							{:else}
								<form class="flex flex-wrap items-end gap-2" onsubmit={schedule}>
									<label class="form-control">
										<span class="label"><span class="label-text">{t('events.lineup.schedule_when')}</span></span>
										<input class="input input-sm w-56 max-w-full" type="datetime-local" required min={toLocalInput(new Date().toISOString())} bind:value={when} />
									</label>
									<button class="btn btn-outline btn-sm" type="submit" disabled={busy || !when || !picked.length}>{t('events.lineup.schedule_button')}</button>
								</form>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		{/if}
	{:else if lineup.status === 'published'}
		<div class="card">
			{#each byPosition(lineup.members.filter((m) => m.selected)) as [position, members] (position)}
				<div class="border-b border-rule px-5 py-3 last:border-b-0">
					<p class="mb-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{position}</p>
					<ul class="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
						{#each members as m (m.member.id)}<li class="flex items-center gap-2 text-sm"><span class="w-7 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span><a class="link font-semibold" href="/members/{m.member.id}">{m.member.name}</a></li>{/each}
					</ul>
				</div>
			{:else}
				<p class="px-5 py-6 text-center text-sm text-muted">{t('events.lineup.none_picked')}</p>
			{/each}
		</div>
	{:else}
		<div class="card card-body items-center py-10 text-center text-muted"><Users size={28} aria-hidden="true" /><p>{t('events.lineup.not_published')}</p></div>
	{/if}
</div>
