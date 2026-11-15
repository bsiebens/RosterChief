<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { ArrowLeft, ChartPie as PieChartIcon, Download, ExternalLink, UserPlus } from '@lucide/svelte';
	import { money } from '#lib/dashboard.ts';
	import AdminsCard from '#lib/components/club/AdminsCard.svelte';
	import BillingCard from '#lib/components/club/BillingCard.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import DangerCard from '#lib/components/club/DangerCard.svelte';
	import DetailsCard from '#lib/components/club/DetailsCard.svelte';
	import FeaturesCard from '#lib/components/club/FeaturesCard.svelte';
	import BarChart from '#lib/components/BarChart.svelte';
	import HomeLocationCard from '#lib/components/club/HomeLocationCard.svelte';
	import Kpi from '#lib/components/Kpi.svelte';
	import PieChart from '#lib/components/PieChart.svelte';
	import StatGroupCard from '#lib/components/StatGroup.svelte';
	import PlanCard from '#lib/components/club/PlanCard.svelte';
	import Alert from '#lib/components/Alert.svelte';

	let { data } = $props();

	const club = $derived(data.club);
	const refresh = () => invalidate('app:club');
	// Everything on this page can be done by staff except what the API reserves for superusers.
	const isSuperuser = $derived(data.auth.status === 'authenticated' && data.auth.me.level === 'superuser');
	const canEdit = true;

	const stats = $derived(data.stats);
	const atLimit = $derived(!!stats?.member_limit && stats.members >= stats.member_limit);
	const nearLimit = $derived(!!stats?.member_limit && stats.members >= stats.member_limit * 0.9);
	const date = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
	// Domains are stored bare ("club.rosterchief.app"); dev ones carry a port.
	const url = (domain: string) => `${domain.startsWith('localhost') || domain.includes('.localhost') ? 'http' : 'https'}://${domain}`;
</script>

<svelte:head><title>{club.name} · Clubs · RosterChief</title></svelte:head>

<div>
	<a class="mb-3 inline-flex items-center gap-1.5 font-mono text-[11px] text-muted hover:text-ink" href="/clubs"><ArrowLeft size={12} aria-hidden="true" /> All clubs</a>
	<div class="flex flex-wrap items-center gap-4">
		<ClubCrest name={club.name} logo={club.logo_url} size={52} />
		<div class="min-w-0 flex-1">
			<div class="flex flex-wrap items-center gap-2">
				<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">{club.name}</h1>
				{#if data.stats?.downgraded_at}
	<Alert kind="warning" title="On the free plan">Downgraded to the free plan since {date(data.stats.downgraded_at)} because of unpaid periods. Paying them restores the plan.</Alert>
{/if}

{#if club.archived_at}<span class="badge badge-warning">archived</span>{:else}<span class="badge badge-success">active</span>{/if}
				{#if club.sport}<span class="badge badge-ghost">{club.sport.name}</span>{/if}
			</div>
			<div class="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted">
				<span>{club.slug}</span>
				{#each club.domains as domain (domain)}
					<a class="inline-flex items-center gap-1 hover:text-ink hover:underline" href={url(domain)} target="_blank" rel="noopener noreferrer">{domain}<ExternalLink size={10} aria-hidden="true" /></a>
				{/each}
				<span>owner {club.owner_email}</span>
				<span>created {date(club.created)}</span>
			</div>
		</div>
	</div>
</div>

{#if club.archived_at}
	<Alert kind="warning" title="Archived">This club has been archived since {date(club.archived_at)}. Its subdomain does not resolve. Restore it under “Danger zone”.</Alert>
{/if}

<!-- Data-dense two-column layout: figures, charts, features and admins in the wide column; details, plan, billing and location beside it, with plan and billing together. -->
<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
	<div class="flex min-w-0 flex-col gap-4">
		{#if stats}
			{@const k = data.overview?.kpis}
			<div class="grid grid-cols-2 gap-3 md:grid-cols-3">
				<Kpi label="members" value={stats.members} sub={stats.member_limit ? `limit ${stats.member_limit}${atLimit ? ' · reached' : nearLimit ? ' · almost' : ''}` : 'no limit'} tone={atLimit ? 'club' : nearLimit ? 'warn' : 'none'} />
				<Kpi label="new members" value={k ? k.new_members_30d : '—'} sub="joined in the last 30 days" />
				<Kpi label="renewal rate" value={k?.renewal_rate == null ? 'N/A' : `${k.renewal_rate}%`} sub={k?.renewal_rate == null ? 'no renewal data yet' : 'of last period’s members'} />
				<Kpi label="administrators" value={stats.admins} tone={stats.admins === 0 ? 'club' : 'none'} sub={stats.admins === 0 ? 'nobody can manage the club' : undefined} />
				<Kpi label="dues owed" value={money(stats.dues_owed)} tone={Number(stats.dues_owed) ? 'warn' : 'none'} />
				<Kpi label="plan" value={stats.plan ?? 'default'} sub="{stats.features.length} feature{stats.features.length === 1 ? '' : 's'} enabled" />
			</div>
		{/if}

		{#if data.overview}
			{@const o = data.overview}
			<div class="grid gap-4 md:grid-cols-2">
				<div class="card p-4">
					<div class="mb-1 flex items-center gap-2 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><UserPlus size={16} aria-hidden="true" /> Signups per month</div>
					<p class="mb-3 font-mono text-[11px] text-muted">Members added over the last 12 months.</p>
					<BarChart series={o.signups} label="Signups" />
				</div>
				<div class="card p-4">
					<div class="mb-1 flex items-center gap-2 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><PieChartIcon size={16} aria-hidden="true" /> Member types</div>
					<p class="mb-3 font-mono text-[11px] text-muted">How the roster splits by type.</p>
					{#if o.member_types.length}
						<PieChart slices={o.member_types.map((t) => ({ label: t.label, value: t.count }))} label="Member types" />
					{:else}
						<p class="flex h-44 items-center justify-center text-sm text-muted">No members yet.</p>
					{/if}
				</div>
			</div>

			<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
				{#each o.groups as group (group.key)}<StatGroupCard {group} />{/each}
			</div>
		{:else}
			<Alert kind="warning">The club's statistics could not be loaded.</Alert>
		{/if}

		<FeaturesCard {club} entitlements={data.entitlements} overrides={data.overrides} {canEdit} onchange={refresh} />
		<AdminsCard {club} {canEdit} onchange={refresh} />
	</div>

	<div class="flex min-w-0 flex-col gap-4">
		<DetailsCard {club} sports={data.sports} countries={data.countries} onchange={refresh} />
		<PlanCard {club} subscriptions={data.subscriptions} plans={data.plans} billing={data.billing} {isSuperuser} {canEdit} onchange={refresh} />
		<BillingCard {club} billing={data.billing} {canEdit} onchange={refresh} archiveDays={data.archiveAfterDays} />
		<HomeLocationCard {club} location={data.overview?.home_location ?? null} countries={data.countries} {canEdit} onchange={refresh} />
		{#if isSuperuser}
			<section class="card flex flex-col gap-3 p-4">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold uppercase"><Download size={16} aria-hidden="true" /> Club backup</h2>
				<p class="text-sm text-muted">Download this club’s records and files for restoration in RosterChief.</p>
				<a class="btn btn-outline self-start" href="/api/platform/v1/clubs/{club.id}/backup" download data-sveltekit-reload>Download backup</a>
			</section>
		{/if}
	</div>
</div>

<DangerCard {club} {isSuperuser} onchange={refresh} />
