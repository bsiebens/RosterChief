<script lang="ts">
	import { goto, invalidate } from '$app/navigation';
	import { Pencil, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Kpi from '#lib/components/Kpi.svelte';
	import Competitions from '#lib/components/sports/Competitions.svelte';
	import RefereeKind from '#lib/components/sports/RefereeKind.svelte';
	import SportDialogs from '#lib/components/sports/SportDialogs.svelte';
	import LineupLines from '#lib/components/sports/LineupLines.svelte';
	import SportList from '#lib/components/sports/SportList.svelte';
	import { NO_SPORT, sportInUse } from '#lib/sports.ts';

	let { data } = $props();

	const refresh = () => invalidate('app:sports');
	const sport = $derived(data.sports.find((s) => s.id === data.selected) ?? null);
	const comps = $derived(data.competitions.filter((c) => (data.selected === NO_SPORT ? !c.sport_id : c.sport_id === data.selected)));
	const noSportCount = $derived(data.competitions.filter((c) => !c.sport_id).length);

	let mode = $state<'add' | 'rename' | 'delete' | null>(null);
	let notice = $state('');

	async function done(message: string, createdId?: string, deleted?: boolean) {
		notice = message;
		if (createdId) await goto(`?sport=${createdId}`, { replaceState: true });
		else if (deleted) await goto('?', { replaceState: true });
		await refresh();
	}
</script>

<svelte:head><title>Sports · RosterChief</title></svelte:head>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Sports &amp; competitions</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">The shared data clubs build on: sports, their referee and official types, and the competitions clubs import fixtures from</p>
</div>

{#if data.sportsError}<Alert kind="error">{data.sportsError}</Alert>{/if}
{#if notice}<Alert kind="success">{notice}</Alert>{/if}

<div class="grid items-start gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
	<SportList sports={data.sports} selected={data.selected} {noSportCount} onadd={() => (mode = 'add')} />

	<div class="flex min-w-0 flex-col gap-4">
		{#if sport}
			<div class="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h2 class="font-display text-2xl leading-none font-extrabold text-ink uppercase">{sport.name}</h2>
					<div class="mt-1 font-mono text-[11px] text-muted">{sport.slug}</div>
				</div>
				<div class="flex flex-wrap items-center gap-2">
					<button class="btn btn-outline btn-sm" onclick={() => (mode = 'rename')}><Pencil size={13} aria-hidden="true" /> Rename</button>
					<button class="btn btn-outline btn-error btn-sm" onclick={() => (mode = 'delete')} aria-describedby={sportInUse(sport) ? 'in-use' : undefined}><Trash2 size={13} aria-hidden="true" /> Delete</button>
				</div>
			</div>
			{#if sportInUse(sport)}
				<p id="in-use" class="text-xs text-muted">Used by {sport.club_count} clubs and {sport.competition_count} competitions, so it cannot be deleted until nothing uses it.</p>
			{/if}
			<div class="grid grid-cols-3 gap-3">
				<Kpi label="Clubs" value={sport.club_count} />
				<Kpi label="Competitions" value={sport.competition_count} />
				<Kpi label="Referee types" value={sport.referee_type_count} />
			</div>

			{#key sport.id}<LineupLines {sport} onchange={refresh} />{/key}

			<div class="card">
				<div class="card-body gap-4">
					<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Referee types</h2>
					{#if data.typesError}<Alert kind="error">{data.typesError}</Alert>{/if}
					<div class="grid gap-5 xl:grid-cols-2">
						<RefereeKind sportId={sport.id} kind="referee" items={data.types.filter((t) => t.kind === 'referee')} onchange={refresh} />
						<RefereeKind sportId={sport.id} kind="official" items={data.types.filter((t) => t.kind === 'official')} onchange={refresh} />
					</div>
				</div>
			</div>
		{:else if data.selected === NO_SPORT}
			<div>
				<h2 class="font-display text-2xl leading-none font-extrabold text-ink uppercase">No sport</h2>
				<p class="mt-1 text-sm text-muted">Competitions that are not tied to a sport.</p>
			</div>
		{:else}
			<div class="card"><div class="card-body text-sm text-muted">Add a sport to get started.</div></div>
		{/if}

		{#if data.competitionsError}<Alert kind="error">{data.competitionsError}</Alert>{/if}
		<Competitions competitions={comps} defaultSportId={sport?.id ?? ''} sports={data.sports} features={data.features} onchange={refresh} />
	</div>
</div>

<SportDialogs bind:mode {sport} ondone={done} />
