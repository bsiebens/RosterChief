<script lang="ts">
	import { invalidate } from '$app/navigation';
	import Alert from '#lib/components/Alert.svelte';
	import Kpi from '#lib/components/Kpi.svelte';
	import CatalogTable from '#lib/components/features/CatalogTable.svelte';
	import EntitlementLog from '#lib/components/features/EntitlementLog.svelte';
	import PlanList from '#lib/components/features/PlanList.svelte';
	import PlanMatrix from '#lib/components/features/PlanMatrix.svelte';
	import SwitchesCard from '#lib/components/features/SwitchesCard.svelte';

	let { data } = $props();

	const canEdit = $derived(data.auth.status === 'authenticated' && data.auth.me.level === 'superuser');
	const refresh = () => invalidate('app:features');
	let view = $state<'plans' | 'matrix'>('plans');

	const subscribers = $derived(data.plans.reduce((n, p) => n + p.subscriber_count, 0));
	const activePlans = $derived(data.plans.filter((p) => p.is_active).length);
	const flags = $derived(
		[data.switches?.maintenance.is_active && 'maintenance on', data.switches?.email_suppression.is_active && 'email paused'].filter(Boolean) as string[]
	);
</script>

<svelte:head><title>Features · RosterChief</title></svelte:head>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Features</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">Plans, the feature catalog, platform switches and the entitlement log</p>
</div>

<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
	<Kpi label="Plans" value={data.plans.length} sub="{activePlans} active" />
	<Kpi label="Subscribers" value={subscribers} sub="clubs on a plan" />
	<Kpi label="Features" value={data.features.length} sub="in the catalog" />
	<Kpi label="Switches" value={flags.length ? flags.length : 'OK'} sub={flags.length ? flags.join(' · ') : 'nothing paused'} tone={data.switches?.maintenance.is_active ? 'club' : flags.length ? 'warn' : 'none'} />
</div>

<section class="flex flex-col gap-3" aria-labelledby="h-switches">
	<h2 id="h-switches" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Platform switches</h2>
	{#if data.switches}
		<SwitchesCard switches={data.switches} {canEdit} onchange={refresh} />
	{:else}
		<Alert kind="error">{data.switchesError}</Alert>
	{/if}
</section>

<section class="flex flex-col gap-3" aria-labelledby="h-plans">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h2 id="h-plans" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Plans</h2>
		<div class="flex gap-1" role="group" aria-label="Plans view">
			<button class="btn btn-xs {view === 'plans' ? '' : 'btn-outline'}" aria-pressed={view === 'plans'} onclick={() => (view = 'plans')}>Plans</button>
			<button class="btn btn-xs {view === 'matrix' ? '' : 'btn-outline'}" aria-pressed={view === 'matrix'} onclick={() => (view = 'matrix')}>Matrix</button>
		</div>
	</div>
	{#if data.plansError}<Alert kind="error">{data.plansError}</Alert>{/if}
	{#if view === 'plans'}
		<PlanList plans={data.plans} catalog={data.features} {canEdit} onchange={refresh} />
	{:else}
		<PlanMatrix plans={data.plans} catalog={data.features} />
	{/if}
</section>

<section class="flex flex-col gap-3" aria-label="Feature catalog">
	{#if data.featuresError}<Alert kind="error">{data.featuresError}</Alert>{/if}
	<CatalogTable catalog={data.features} />
</section>

<EntitlementLog log={data.log} total={data.logTotal} error={data.logError} clubs={data.clubs} club={data.query.club} pageNo={data.query.page} pageSize={data.pageSize} />
