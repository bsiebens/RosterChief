<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { ArrowLeft, BarChart3, Pencil, Trash2, TrendingDown, TrendingUp, TriangleAlert, Upload, UserMinus, UserPlus, UserX } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import NumberLegend from '#lib/components/teams/NumberLegend.svelte';
	import RosterModal from '#lib/components/teams/RosterModal.svelte';
	import StaffModal from '#lib/components/teams/StaffModal.svelte';
	import TeamFormModal from '#lib/components/teams/TeamFormModal.svelte';
	import FixtureSourcesCard from '#lib/components/teams/FixtureSourcesCard.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canManageSettings, canManageTeam } from '#lib/permissions.ts';
	import { kindIcon } from '#lib/officiating-icons.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { eventHref, formatEventDate, formatRate, memberHref, rateTone, TONE } from '#lib/attendance.ts';
	import { TILE_STYLE, type NumberStatus } from '#lib/teams/status.ts';
	import type { RosterEntry, StaffEntry } from '#lib/teams/server.ts';
	import { analyticsHref } from '#lib/analytics.ts';

	let { data, form } = $props();
	const team = $derived(data.team);
	const season = $derived(data.season);
	const seasonId = $derived(team?.season_id ?? '');
	// Every form keeps the selected season in the URL.
	const act = (name: string) => `?/${name}${season ? `&season=${season}` : ''}`;

	let editOpen = $state(false);
	let deleteOpen = $state(false);
	let addPlayerOpen = $state(false);
	let editPlayerOpen = $state(false);
	let editPlayer = $state<RosterEntry | null>(null);
	let addStaffOpen = $state(false);
	let editStaffOpen = $state(false);
	let editStaff = $state<StaffEntry | null>(null);
	let busy = $state(false);

	const playerPositions = $derived((data.positions ?? []).filter((p) => !p.staff_position));
	const staffPositions = $derived((data.positions ?? []).filter((p) => p.staff_position));
	const errorFor = (a: string) => (form?.teamAction === a ? (form.teamError as string) : undefined);
	const fullName = (p: { first_name: string; last_name: string }) => `${p.first_name} ${p.last_name}`.trim();

	const submit = () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
		};
	};

	const watermark = $derived(data.branding?.logo_url || '/images/rosterchief-white.svg');
	const addHref = $derived(`/teams/${team?.id}/add${season ? `?season=${season}` : ''}`);
	const manage = $derived(canManageTeam(data.me, team?.id));
	const seasonObj = $derived((data.seasons ?? []).find((x) => x.id === seasonId));
	const seasonName = $derived(seasonObj?.name ?? '');
	const sinceYear = $derived(seasonObj?.start_date ? new Date(seasonObj.start_date).getFullYear() : null);
	// Attendance statistics of the season (null when they could not be loaded: the cards then say there is no data).
	const attendance = $derived(data.attendance);
	const hasRate = $derived(attendance?.rate !== null && attendance?.rate !== undefined);
	const rankings = $derived([
		{ key: 'top', list: attendance?.top_attenders ?? [], Icon: TrendingUp },
		{ key: 'attention', list: attendance?.needs_attention ?? [], Icon: TrendingDown }
	]);
	const isSettings = $derived(canManageSettings(data.me));

	// Next to the name in the top bar: the age range and where the team feeds into.
	const ageRange = $derived(team ? (team.age_min === null && team.age_max === null ? '' : team.age_min !== null && team.age_max !== null ? `${team.age_min}–${team.age_max}` : team.age_min !== null ? `${team.age_min}+` : `≤ ${team.age_max}`) : '');
	const headerContext = $derived([ageRange ? t('teampage.ages', { range: ageRange }) : '', team?.feeds_into_name ? t('teampage.feeds', { name: team.feeds_into_name }) : ''].filter(Boolean).join(' · '));

	usePageHeader(() => ({ title: team?.name ?? '', context: headerContext || t('teams.detail.context'), actions }));

	// Numbers card: every number of the pool, from this team's point of view.
	const numbers = $derived(data.numbers);
	const tiles = $derived.by(() => {
		const n = numbers;
		if (!n?.pool) return [];
		const mine = new Map<number, string[]>();
		for (const p of n.players) if (p.jersey_number !== null) mine.set(p.jersey_number, [...(mine.get(p.jersey_number) ?? []), fullName(p)]);
		const free = new Set(n.free_numbers);
		const out: { number: number; status: NumberStatus; title: string }[] = [];
		for (let i = n.pool.min_number; i <= n.pool.max_number; i++) {
			const others = n.taken_by_others[String(i)] ?? [];
			let status: NumberStatus = 'free';
			let title = t('teams.num.free');
			if (mine.has(i)) {
				status = (mine.get(i)!.length > 1 ? 'conflict' : 'yours');
				title = mine.get(i)!.join(', ');
			} else if (others.length) {
				status = 'taken';
				title = others.map((h) => `${fullName(h)} (${h.team_name})`).join(', ');
			} else if (n.reserved[String(i)] !== undefined) {
				status = 'reserved';
				title = n.reserved[String(i)] || t('teams.num.reserved');
			} else if (!free.has(i)) {
				status = 'taken';
				title = t('teams.num.taken');
			}
			out.push({ number: i, status, title });
		}
		return out;
	});
</script>

{#snippet actions()}
	<select class="select w-auto" aria-label={t('teams.season')} value={season || seasonId} onchange={(e) => goto(`?season=${e.currentTarget.value}`, { reset: false })}>
		{#each data.seasons ?? [] as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
	</select>
	<a class="btn btn-outline gap-2" href="/teams"><ArrowLeft size={16} /> {t('teams.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(team?.name ?? '')}</title></svelte:head>

{#if team}
<div class="grid gap-5">
	<!-- Header: the team photo of the season (when there is one) behind the name, with the upload controls. -->
	<section class="card relative isolate overflow-hidden border-ink bg-ink text-white">
		{#if team.photo_url}
			<img src={team.photo_url} alt="" class="absolute inset-0 -z-20 h-full w-full object-cover" />
			<div class="absolute inset-0 -z-10 bg-gradient-to-t from-ink via-ink/55 to-ink/25"></div>
		{:else}
			<!-- No photo: the club logo (else the RosterChief one) as a big, light grayscale watermark clipped on the right half. -->
			<!-- The logo in light grey, faint, on top of the header background; its own transparency is kept. -->
			<img src={watermark} alt="" aria-hidden="true" class="pointer-events-none absolute top-1/2 -right-12 -z-10 h-[156%] w-auto max-w-none -translate-y-1/2 object-contain opacity-[0.2] [filter:grayscale(1)_brightness(1.7)]" />
		{/if}
		<div class="flex min-h-[16rem] flex-col justify-between gap-8 p-7">
			<div class="flex flex-wrap items-center justify-end gap-3 text-sm text-on-dark">
				{#if !team.photo_url}<span>{t('teampage.no_photo')}</span>{/if}
				{#if manage}
					<form method="POST" action={act('uploadPhoto')} enctype="multipart/form-data" use:enhance={submit}>
						<label class="btn btn-sm cursor-pointer gap-2 border-white/25 bg-white/10 text-white hover:bg-white/20">
							<Upload size={14} aria-hidden="true" />
							{team.photo_url ? t('teampage.change') : t('teampage.upload')}
							<input type="file" name="file" accept="image/jpeg,image/png,image/webp" class="sr-only" onchange={(e) => e.currentTarget.form?.requestSubmit()} />
						</label>
					</form>
					{#if team.photo_url}
						<form method="POST" action={act('removePhoto')} use:enhance={submit}>
							<button class="btn btn-sm gap-2 border-white/25 bg-white/10 text-white hover:bg-white/20" type="submit" disabled={busy}><Trash2 size={14} aria-hidden="true" /> {t('teampage.remove_photo')}</button>
						</form>
					{/if}
				{/if}
			</div>
			<div class="flex flex-wrap items-end justify-between gap-4">
				<div>
					{#if seasonName}<p class="font-mono text-xs font-bold tracking-[.12em] text-ice uppercase">{t('teampage.season', { name: seasonName })}</p>{/if}
					<h2 class="font-display text-5xl leading-none font-extrabold uppercase">{team.name}</h2>
					<p class="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-on-dark">
						<span>{team.roster.length === 1 ? t('teampage.player_one') : t('teampage.players', { n: team.roster.length })}</span>
						<span>{team.staff.length === 1 ? t('teampage.staff_one') : t('teampage.staff', { n: team.staff.length })}</span>
						{#if team.feeds_into_name}<span>{t('teampage.feeds', { name: team.feeds_into_name })}</span>{/if}
					</p>
				</div>
				<div class="flex flex-wrap gap-2">
					{#if team.analytics_available}<a class="btn gap-2 border-white/25 bg-white/10 text-white hover:bg-white/20" href={analyticsHref(team.id, season || seasonId)}><BarChart3 size={14} aria-hidden="true" /> {t('analytics.button')}</a>{/if}
					{#if manage}<button class="btn gap-2 border-white/25 bg-white/10 text-white hover:bg-white/20" type="button" onclick={() => (editOpen = true)}><Pencil size={14} /> {t('common.edit')}</button>{/if}
					{#if isSettings}<button class="btn gap-2 border-white/25 bg-white/10 text-white hover:bg-white/20" type="button" onclick={() => (deleteOpen = true)}><Trash2 size={14} /> {t('common.delete')}</button>{/if}
					{#if manage}<a class="btn btn-primary gap-2" href={addHref}><UserPlus size={16} /> {t('teampage.add.button')}</a>{/if}
				</div>
			</div>
		</div>
	</section>
	{#if form?.teamAction === 'photo' && form.teamError}<Alert kind="error" title={t('teampage.photo_failed')}>{form.teamError}</Alert>{/if}

	<!-- Attendance of the season: turnout = present / (present + absent) over the team's past events. -->
	<div class="grid gap-4 lg:grid-cols-3">
		<div class="card card-body">
			<span class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('teampage.att.rate')}</span>
			<span class="font-display text-5xl leading-none font-extrabold {hasRate ? TONE[rateTone(attendance?.rate)].text : 'text-muted'}">{formatRate(attendance?.rate)}</span>
			{#if attendance && attendance.sessions > 0}
				<span class="text-sm text-muted">{attendance.sessions === 1 ? t('attbox.based_on_one') : t('attbox.based_on', { n: attendance.sessions })}</span>
				{#if hasRate}<div class="mt-1 h-1.5 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full {TONE[rateTone(attendance.rate)].bar}" style="width: {attendance.rate}%"></div></div>{/if}
			{:else}
				<span class="text-sm text-muted">{t('teampage.att.no_data')}</span>
			{/if}
		</div>
		{#each rankings as { key, list, Icon } (key)}
			<div class="card card-body gap-2">
				<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Icon size={16} aria-hidden="true" /> {t(`teampage.att.${key}`)}</h2>
				{#each list as p (p.id)}
					<div class="flex items-baseline justify-between gap-3 text-sm">
						<a class="link truncate font-semibold" href={memberHref(p.id)}>{p.name}</a>
						<span class="rounded border px-1.5 font-mono text-xs {TONE[rateTone(p.rate)].pill}" title={t('attbox.responses', { n: p.responses })}>{formatRate(p.rate)}</span>
					</div>
				{:else}
					<p class="py-3 text-center text-sm text-muted">{t('teampage.att.no_data')}</p>
				{/each}
			</div>
		{/each}
	</div>
	<div class="grid gap-4 lg:grid-cols-2">
		<div class="card card-body gap-2">
			<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><TriangleAlert size={16} aria-hidden="true" /> {t('teampage.att.missed')}</h2>
			{#if !attendance}
				<p class="py-3 text-center text-sm text-muted">{t('teampage.att.no_data')}</p>
			{:else}
				{#each attendance.missed_recent_practices as p (p.id)}<a class="link text-sm font-semibold" href={memberHref(p.id)}>{p.name}</a>{:else}<p class="py-3 text-center text-sm text-muted">{t('teampage.att.no_one')}</p>{/each}
			{/if}
		</div>
		<div class="card card-body gap-2">
			<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><UserX size={16} aria-hidden="true" /> {t('teampage.att.noshows')}{#if attendance?.no_show_count}&nbsp;<span class="font-mono text-xs font-normal text-muted">· {attendance.no_show_count}</span>{/if}</h2>
			{#if !attendance}
				<p class="py-3 text-center text-sm text-muted">{t('teampage.att.no_data')}</p>
			{:else}
				{#each attendance.no_shows as n (`${n.member.id}-${n.event.id}`)}
					<div class="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
						<a class="link font-semibold" href={memberHref(n.member.id)}>{n.member.name}</a>
						<span class="min-w-0 truncate text-muted"><a class="link" href={eventHref(n.event.id)}>{n.event.title}</a> · <span class="font-mono text-xs">{formatEventDate(n.event.start)}</span></span>
					</div>
				{:else}
					<p class="py-3 text-center text-sm text-muted">{t('teampage.att.none_recorded')}</p>
				{/each}
				{#if attendance.no_show_count > attendance.no_shows.length}<p class="text-xs text-muted">{t('attbox.noshow_more', { n: attendance.no_show_count - attendance.no_shows.length })}</p>{/if}
			{/if}
		</div>
	</div>

	<div class="card">
		<div class="flex flex-wrap items-center justify-between gap-2 px-6 py-4">
			<h2 class="font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase">{t('teams.card.players')} <span class="font-mono text-xs font-normal text-muted">· {team.roster.length}</span></h2>
			{#if manage}<a class="btn btn-outline btn-sm gap-2" href={addHref}><UserPlus size={16} /> {t('teams.player.add_many')}</a>{/if}
		</div>
		{#if errorFor('removePlayer')}<div class="px-6 pb-3"><Alert kind="error">{errorFor('removePlayer')}</Alert></div>{/if}
		<div class="overflow-x-auto">
			<table class="table">
				<thead>
					<tr><th class="w-20">{t('teams.f.number')}</th><th>{t('teams.col.player')}</th><th>{t('teams.f.position')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each team.roster as p (p.id)}
						<tr class="hover:bg-surface">
							<td class="font-display text-xl font-extrabold text-ink">{p.jersey_number ?? '—'}</td>
							<td>
								<div class="flex items-center gap-3">
									<Avatar first_name={p.first_name} last_name={p.last_name} photoUrl={p.photo_url} />
									<a class="link font-semibold" href="/members/{p.member_id}">{p.first_name} {p.last_name}</a>
									{#if p.is_captain}<span class="badge badge-primary badge-sm" title={t('teams.captain')}>C</span>{/if}
									{#if p.is_alternate_captain}<span class="badge badge-outline badge-sm" title={t('teams.alt_captain')}>A</span>{/if}
								</div>
							</td>
							<td class="text-sm text-muted">{p.position_name ?? '—'}</td>
							<td>
								{#if manage}
								<div class="flex items-center justify-end gap-1.5">
									<button class="btn btn-outline btn-xs gap-1" type="button" aria-label={t('teams.player.edit_named', { name: fullName(p) })} onclick={() => ((editPlayer = p), (editPlayerOpen = true))}><Pencil size={13} /> {t('common.edit')}</button>
									<form method="POST" action={act('removePlayer')} use:enhance={submit}>
										<input type="hidden" name="membership_id" value={p.id} />
										<button class="btn btn-outline btn-error btn-xs gap-1" type="submit" disabled={busy} aria-label={t('teams.player.remove_named', { name: fullName(p) })}><UserMinus size={13} /> {t('families.remove')}</button>
									</form>
								</div>
								{/if}
							</td>
						</tr>
					{:else}
						<tr><td colspan="4" class="py-6 text-center text-muted">{t('teams.player.none')}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>

	<div class="card">
		<div class="flex flex-wrap items-center justify-between gap-2 px-6 py-4">
			<h2 class="font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase">{t('teams.card.staff')} <span class="font-mono text-xs font-normal text-muted">· {team.staff.length}</span></h2>
			{#if manage}<a class="btn btn-outline btn-sm gap-2" href={addHref}><UserPlus size={16} /> {t('teams.staff.add_many')}</a>{/if}
		</div>
		{#if errorFor('removeStaff')}<div class="px-6 pb-3"><Alert kind="error">{errorFor('removeStaff')}</Alert></div>{/if}
		<ul class="divide-y divide-rule border-t border-line">
			{#each team.staff as s (s.id)}
				<li class="flex items-center gap-4 px-6 py-3">
					<Avatar first_name={s.first_name} last_name={s.last_name} photoUrl={s.photo_url} size="h-10 w-10" text="text-sm" />
					<div class="min-w-0 flex-1">
						<a class="link font-semibold" href="/members/{s.member_id}">{s.first_name} {s.last_name}</a>
						<p class="text-sm text-muted">{s.position_name ?? '—'}</p>
					</div>
					{#if sinceYear}<span class="font-mono text-xs text-muted">{t('teampage.since', { year: sinceYear })}</span>{/if}
					{#if manage}
						<div class="flex items-center gap-1.5">
							<button class="btn btn-outline btn-xs btn-square" type="button" title={t('common.edit')} aria-label={t('teams.staff.edit_named', { name: fullName(s) })} onclick={() => ((editStaff = s), (editStaffOpen = true))}><Pencil size={13} /></button>
							<form method="POST" action={act('removeStaff')} use:enhance={submit}>
								<input type="hidden" name="assignment_id" value={s.id} />
								<button class="btn btn-outline btn-error btn-xs btn-square" type="submit" disabled={busy} title={t('teams.staff.remove_named', { name: fullName(s) })} aria-label={t('teams.staff.remove_named', { name: fullName(s) })}><UserMinus size={13} /></button>
							</form>
						</div>
					{/if}
				</li>
			{:else}
				<li class="px-6 py-6 text-center text-muted">{t('teams.staff.none')}</li>
			{/each}
		</ul>
	</div>

	<div class="card card-body">
		<h2 class="font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase">{t('teams.card.numbers')}{#if numbers?.pool} <span class="font-mono text-xs font-normal text-muted">· {numbers.pool.name} ({numbers.pool.min_number}–{numbers.pool.max_number})</span>{/if}</h2>
		{#if !team.pool_id}
			<p class="text-sm text-muted">{t('teams.num.no_pool')}</p>
		{:else if !numbers?.pool}
			<p class="text-sm text-muted">{t('teams.num.unavailable')}</p>
		{:else}
			<div class="mt-2 flex flex-wrap gap-1.5">
				{#each tiles as tile (tile.number)}
					<span class="inline-flex h-9 min-w-9 items-center justify-center rounded border px-1 font-mono text-xs" style={TILE_STYLE[tile.status]} title={tile.title}>{tile.number}</span>
				{/each}
			</div>
			<div class="mt-2"><NumberLegend statuses={['free', 'yours', 'taken', 'reserved', 'conflict']} /></div>
		{/if}
	</div>

	{#if data.referees !== null}
		<div class="grid gap-4 lg:grid-cols-2">
			{#each [{ kind: 'referee', list: data.referees ?? [] }, { kind: 'official', list: data.officials ?? [] }] as const as group (group.kind)}
				{@const KindIcon = kindIcon[group.kind]}
				<div class="card card-body h-full gap-2">
					<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase"><KindIcon size={18} aria-hidden="true" /> {t(`teampage.${group.kind}s.title`)} <span class="font-mono text-xs font-normal text-muted">· {group.list.length}</span></h2>
					<p class="text-sm text-muted">{t(`teampage.${group.kind}s.help`)}</p>
					{#if group.list.length}
						<!-- one or two people stretch out; from three on it is three columns (two where the card is narrow) -->
						<ul class="mt-1 grid gap-2 sm:[grid-template-columns:repeat(var(--cols2),minmax(0,1fr))] xl:[grid-template-columns:repeat(var(--cols3),minmax(0,1fr))]" style="--cols2: {Math.min(group.list.length, 2)}; --cols3: {Math.min(group.list.length, 3)}">
							{#each group.list as r (r.member_id)}
								<li class="flex items-center gap-3 rounded-lg border border-line p-3">
									<Avatar first_name={r.first_name} last_name={r.last_name} photoUrl={r.photo_url} size="h-9 w-9" text="text-xs" />
									<div class="min-w-0">
										<a class="link block truncate font-semibold" href="/members/{r.member_id}">{r.first_name} {r.last_name}</a>
										<p class="truncate text-xs text-muted">{r.level_name}{#if r.valid_until}&nbsp;·&nbsp;{t('teampage.referees.valid_until', { date: formatDate(r.valid_until) })}{/if}</p>
									</div>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="text-sm text-muted">{t('teampage.referees.none')}</p>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	<FixtureSourcesCard teamId={team.id} {seasonId} fixtures={data.fixtures} canImportFallback={isSettings} />
</div>

<TeamFormModal bind:open={editOpen} title={t('teams.edit')} {team} pools={data.pools} teams={data.teams} action={act('save')} name="save" {form} submitLabel={t('common.save')} lockStructure={!canManageSettings(data.me)} />

<RosterModal bind:open={editPlayerOpen} entry={editPlayer} positions={playerPositions} exclude={[]} {act} {seasonId} {form} />
<StaffModal bind:open={editStaffOpen} entry={editStaff} positions={staffPositions} exclude={[]} {act} {seasonId} {form} />

<Modal bind:open={deleteOpen} title={t('teams.delete')}>
	<form method="POST" action="?/delete" use:enhance={submit}>
		{#if errorFor('delete')}<div class="mb-3"><Alert kind="error">{errorFor('delete')}</Alert></div>{/if}
		<p class="text-sm">{t('teams.delete_body', { name: team.name })}</p>
		<div class="modal-action mt-4 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('teams.delete')}</button>
		</div>
	</form>
</Modal>
{/if}
