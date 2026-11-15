<script lang="ts">
	import { CalendarClock, Check, Megaphone, Plus, Save, Trash2, Users, X } from '@lucide/svelte';
	import LineupMeta from '#lib/components/events/LineupMeta.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api, type ApiCall } from '#lib/management-api.ts';
	import { formatDateTime, statusLabelKey, statusStyle, toLocalInput, type Lineup, type LineupMember, type LineupPublishOut } from '#lib/events/types.ts';

	/** The Line-up tab (games and tournaments): pick, save, publish or schedule; a read-only list for everyone else. */
	let { eventId, initial, cancelled, mobile = false }: { eventId: string; initial: Lineup; cancelled: boolean; mobile?: boolean } = $props();

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
	let view = $state<'players' | 'picked'>('players');
	let query = $state('');
	let onlyAvailable = $state(false);
	const q = $derived(query.trim().toLocaleLowerCase());
	const isOut = (m: LineupMember) => ['absent', 'excused', 'dropout'].includes(m.status);
	const toggle = (m: LineupMember) => {
		if (picked.includes(m.member.id)) drop(m.member.id);
		else if (m.available) picked = [...picked, m.member.id];
	};

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
		// Players who are out go last in their position (the sort is stable: roster order is kept otherwise).
		return [...groups.entries()].map(([k, list]) => [k, [...list].sort((a, b) => Number(b.available) - Number(a.available))] as [string, LineupMember[]]);
	}
	const groups = $derived(byPosition(lineup.members));
	const shownGroups = $derived(
		groups
			.map(([position, members]) => [position, members.filter((m) => (!onlyAvailable || m.available) && (!q || m.member.name.toLocaleLowerCase().includes(q)))] as [string, LineupMember[]])
			.filter(([, members]) => members.length)
	);
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

{#snippet mobileEditor()}
	<div class="grid min-w-0 gap-3">
		<div class="grid grid-cols-2 gap-1 rounded-box bg-subhead p-1" role="group" aria-label={t('events.tab.lineup')}>
			{#each [['players', t('coach.lu_view_players')], ['picked', t('coach.lu_view_picked', { n: picked.length })]] as [key, label] (key)}
				<button type="button" class="min-h-10 rounded-md px-3 font-display text-sm font-bold tracking-wide uppercase {view === key ? 'bg-(--portal-panel) text-ink shadow-sm' : 'text-muted'}" aria-pressed={view === key} onclick={() => (view = key as typeof view)}>{label}</button>
			{/each}
		</div>
		{#if view === 'players'}
			<section class="card overflow-hidden">
				<div class="flex items-center gap-2 border-b border-line px-3.5 py-2">
					<label class="sr-only" for="lu-search">{t('portal.search_players')}</label>
					<input id="lu-search" type="search" class="input min-w-0 flex-1" placeholder={t('portal.search_players')} bind:value={query} />
					<button type="button" class="portal-person compact text shrink-0 !px-3" aria-pressed={onlyAvailable} onclick={() => (onlyAvailable = !onlyAvailable)}>{t('coach.lu_available')}</button>
				</div>
				{#each shownGroups as [position, members] (position)}
					<div>
						<p class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{position}</p>
						<ul class="divide-y divide-rule">
							{#each members as m (m.member.id)}
								{@const on = picked.includes(m.member.id)}
								{@const locked = !m.available && !on}
								<li>
									<button type="button" class="flex min-h-16 w-full items-center gap-3 px-3.5 py-2 text-left {locked ? 'cursor-not-allowed bg-subhead/60 opacity-60' : ''} {on ? 'bg-club/10' : ''}" disabled={locked || busy} aria-pressed={on} aria-label={on ? t('events.lineup.drop_named', { name: m.member.name }) : t('coach.lu_add', { name: m.member.name })} onclick={() => toggle(m)}>
										<Avatar first_name={m.member.name.split(' ')[0] ?? ''} last_name={m.member.name.split(' ').slice(1).join(' ')} size="size-9" text="text-xs" />
										<span class="min-w-0 flex-1">
											<span class="flex items-center gap-1.5">
												<span class="w-6 shrink-0 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
												<span class="truncate text-sm font-semibold text-ink {isOut(m) ? 'line-through decoration-1' : ''}">{m.member.name}</span>
												{#if !m.on_roster}<span class="badge badge-xs badge-outline shrink-0">{t('events.lineup.guest')}</span>{/if}
											</span>
											<span class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
												<span class="badge badge-xs border {statusStyle(m.status)}">{t(statusLabelKey(m.status))}</span>
												<LineupMeta {m} class="min-w-0 flex-1" />
											</span>
										</span>
										<span class="grid size-10 shrink-0 place-items-center rounded-lg border {on ? 'border-ok bg-ok text-[#fff]' : 'border-line bg-(--portal-panel) text-muted'}" aria-hidden="true">
											{#if on}<Check size={18} />{:else if !locked}<Plus size={18} />{:else}<X size={16} />{/if}
										</span>
									</button>
								</li>
							{/each}
						</ul>
					</div>
				{:else}
					<p class="px-4 py-6 text-center text-sm text-muted">{lineup.members.length ? t('portal.no_players') : t('events.lineup.no_players')}</p>
				{/each}
			</section>
		{:else}
			<section class="card overflow-hidden">
				{#each pickedGroups as [position, members] (position)}
					<p class="bg-subhead px-3.5 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{position}</p>
					<ul class="divide-y divide-rule">
						{#each members as m (m.member.id)}
							<li class="flex min-h-14 items-center gap-3 px-3.5 py-2">
								<span class="w-6 shrink-0 text-right font-display font-extrabold text-ink">{m.jersey_number ?? ''}</span>
								<span class="min-w-0 flex-1"><span class="block truncate text-sm font-semibold text-ink">{m.member.name}</span><LineupMeta {m} /></span>
								<button type="button" class="btn btn-outline btn-square min-h-11 shrink-0 text-muted" disabled={busy} aria-label={t('events.lineup.drop_named', { name: m.member.name })} onclick={() => drop(m.member.id)}><Trash2 size={17} aria-hidden="true" /></button>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="px-4 py-6 text-center text-sm text-muted">{t('events.lineup.none_picked')}</p>
				{/each}
			</section>
			{#if !lineup.published_at}
				<details class="card card-body gap-2" open={!!lineup.scheduled_publish_at}>
					<summary class="flex min-h-10 cursor-pointer items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><CalendarClock size={16} aria-hidden="true" /> {t('events.lineup.schedule')}</summary>
					{#if lineup.scheduled_publish_at}
						<p class="mt-2 text-sm">{t('events.lineup.scheduled_at', { when: formatDateTime(lineup.scheduled_publish_at) })}</p>
						<button class="btn btn-outline min-h-11 gap-2 self-start" type="button" disabled={busy} onclick={unschedule}><X size={14} /> {t('events.lineup.unschedule')}</button>
					{:else}
						<form class="mt-2 grid gap-2" onsubmit={schedule}>
							<label class="grid gap-1 text-sm font-semibold">{t('events.lineup.schedule_when')}
								<input class="input w-full font-normal" type="datetime-local" required min={toLocalInput(new Date().toISOString())} bind:value={when} />
							</label>
							<button class="btn btn-outline min-h-11" type="submit" disabled={busy || !when || !picked.length}>{t('events.lineup.schedule_button')}</button>
						</form>
					{/if}
				</details>
			{/if}
		{/if}
		<p class="text-xs text-muted">{lineup.published_at ? t('events.lineup.notify_hint_published') : t('events.lineup.notify_hint')}</p>
		<div class="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 grid gap-1.5 rounded-box border border-line bg-(--portal-panel) p-2.5 shadow-sm">
			{#if dirty}<p class="text-center text-xs font-semibold text-warn-text">{t('coach.lu_unsaved')} · {t('events.lineup.picked', { n: picked.length })}</p>{/if}
			<div class="flex items-center gap-2">
				<button class="btn btn-outline min-h-12 flex-1 gap-2" type="button" disabled={!dirty || busy} onclick={save}><Save size={16} /> {t('events.lineup.save')}</button>
				<button class="btn btn-primary min-h-12 flex-1 gap-2" type="button" disabled={busy || (!picked.length && !lineup.published_at)} onclick={publish}><Megaphone size={16} /> {lineup.published_at ? t('events.lineup.publish_changes') : t('events.lineup.publish')}</button>
			</div>
		</div>
	</div>
{/snippet}

<div class="grid min-w-0 gap-4">
	<!-- Managers see the status on their Players card; everyone else gets it here. -->
	<div class="flex flex-wrap items-center gap-3 {manage && lineup.team && !mobile ? 'hidden' : ''}">
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
		{:else if mobile}
			{@render mobileEditor()}
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
											<span class="min-w-0 flex-1"><span class="block truncate text-sm font-semibold text-ink {isOut(m) ? 'line-through decoration-1' : ''}">{m.member.name}</span><LineupMeta {m} /></span>
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
