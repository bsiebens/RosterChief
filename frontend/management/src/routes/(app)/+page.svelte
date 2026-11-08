<script lang="ts">
	import { ArrowRight } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { signOut } from '#lib/session.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import FeeStatusCard from '#lib/components/overview/FeeStatusCard.svelte';
	import KpiBar from '#lib/components/overview/KpiBar.svelte';
	import NewsCard from '#lib/components/overview/NewsCard.svelte';
	import NeedsAttention from '#lib/components/overview/NeedsAttention.svelte';
	import RenewalCard from '#lib/components/overview/RenewalCard.svelte';
	import SignupsChart from '#lib/components/overview/SignupsChart.svelte';
	import SummaryCards from '#lib/components/overview/SummaryCards.svelte';
	import UpcomingCard from '#lib/components/overview/UpcomingCard.svelte';

	let { data } = $props();

	const club = $derived(data.mode === 'club' ? data.branding : null);
	const first = $derived(data.me?.first_name ?? '');
	// The shop card needs the shop feature on the club and the shop.manage_shop permission.
	const showShop = $derived(!!data.me?.features?.includes('shop') && !!data.me?.permissions?.includes('shop.manage_shop'));

	usePageHeader(() => ({ title: club ? club.name : t('home.picker.title'), badge: club && data.overview?.season ? seasonBadge : null }));
</script>

{#snippet seasonBadge()}
	<span class="badge badge-success self-center">{data.overview?.season?.name}</span>
{/snippet}

<svelte:head><title>{pageTitle(club ? t('shell.nav.overview') : t('home.picker.title'))}</title></svelte:head>

{#if club}
	{#if data.overview?.kpis && data.overview.cards}
		{@const o = data.overview}
		<div class="grid gap-4">
			<KpiBar overview={o} />
			<!-- Left: news, signups, fees + renewal. Right: needs attention (as tall as the news), then the fixtures down to the bottom of the renewal card. -->
			<div class="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
				<NewsCard items={data.news ?? []} pending={data.newsPending ?? 0} />
				<NeedsAttention overview={o} noTypeCount={data.noTypeCount} />
				<SignupsChart months={o.signups} />
				<div class="xl:col-start-2 xl:row-span-2 xl:row-start-2 [&>*]:h-full"><UpcomingCard events={data.upcoming ?? []} /></div>
				{#if o.fees}
					<div class="grid gap-4 md:grid-cols-2 xl:col-start-1 xl:row-start-3">
						<FeeStatusCard fees={o.fees} />
						<RenewalCard renewal={o.renewal} />
					</div>
				{/if}
			</div>
			<SummaryCards cards={o.cards!} {showShop} />
		</div>
	{:else}
		<div class="card">
			<div class="card-body gap-1">
				<h2 class="font-display text-xl font-extrabold tracking-[.04em] text-ink uppercase">{first ? t('home.welcome_name', { name: first }) : t('home.welcome')}</h2>
				<p class="text-sm text-muted">{t('home.welcome_body', { club: club.name })}</p>
			</div>
		</div>
	{/if}
{:else if data.clubs.length === 0}
	<div class="card max-w-xl">
		<div class="card-body gap-3">
			<h2 class="font-display text-xl font-extrabold tracking-[.04em] text-ink uppercase">{t('home.no_clubs.title')}</h2>
			<p class="text-sm text-muted">{t('home.no_clubs.body')}</p>
			<button class="btn btn-outline self-start" onclick={signOut}>{t('shell.menu.logout')}</button>
		</div>
	</div>
{:else}
	<p class="text-sm text-muted">{t('home.picker.lead')}</p>
	<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each data.clubs as c (c.slug)}
			<a class="card flex flex-row items-center gap-4 p-4 transition-colors hover:border-club" href={`/go/${c.slug}`} data-sveltekit-reload>
				{#if c.primary_color}<span class="h-8 w-1 shrink-0 rounded-full" style="background:{c.primary_color}" aria-hidden="true"></span>{/if}
				<ClubCrest name={c.name} logoUrl={c.logo_url} size={44} bg={c.primary_color || undefined} fg={c.primary_content_color || undefined} />
				<span class="min-w-0 flex-1">
					<span class="block truncate font-display text-lg leading-tight font-extrabold tracking-[.04em] text-ink uppercase">{c.name}</span>
					<span class="block truncate font-mono text-[11px] text-muted">{c.host}</span>
				</span>
				<ArrowRight size={16} class="shrink-0 text-dim" aria-hidden="true" />
			</a>
		{/each}
	</div>
{/if}
