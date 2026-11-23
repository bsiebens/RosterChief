<script lang="ts">
	import { CalendarDays, Download, ExternalLink, FileText, Mail, MapPin, Users } from '@lucide/svelte';
	import GameCard from '#lib/components/tournaments/public/GameCard.svelte';
	import GroupTable from '#lib/components/tournaments/public/GroupTable.svelte';
	import Podium from '#lib/components/tournaments/public/Podium.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dayLabel, ties } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const base = $derived(`/tournaments/${tn.path}`);
	const live = $derived(ties(tn.live_matches));
	const next = $derived(ties(tn.upcoming_matches));
	const recent = $derived(ties(tn.recent_matches).reverse());
	const finished = $derived(tn.final_standings.length > 0);
	const tables = $derived(tn.standings.flatMap((s) => s.tables.filter((tb) => tb.rows.length).map((tb) => ({ s, tb }))));
	const age = $derived(
		tn.earliest_birth_year !== null && tn.latest_birth_year !== null
			? t('ts.born_range', { earliest: tn.earliest_birth_year, latest: tn.latest_birth_year })
			: tn.earliest_birth_year !== null
				? t('ts.born_from', { year: tn.earliest_birth_year })
				: tn.latest_birth_year !== null
					? t('ts.born_until', { year: tn.latest_birth_year })
					: ''
	);
	// an embedded street map around the venue when the club has its coordinates
	const mapSrc = $derived.by(() => {
		if (tn.latitude == null || tn.longitude == null) return null;
		const d = 0.006;
		return `https://www.openstreetmap.org/export/embed.html?bbox=${tn.longitude - d},${tn.latitude - d / 1.6},${tn.longitude + d},${tn.latitude + d / 1.6}&layer=mapnik&marker=${tn.latitude},${tn.longitude}`;
	});
</script>

<svelte:head>
	<title>{pageTitle(tn.name)}</title>
	{#if tn.status === 'draft'}<meta name="robots" content="noindex, nofollow" />{/if}
</svelte:head>

{#if tn.status === 'draft'}<p class="rounded-lg border border-line bg-white px-4 py-2 text-sm text-muted" role="status">{t('tp.draft_note')}</p>{/if}

{#if live.length}
	<section aria-labelledby="ts-live">
		<h2 id="ts-live" class="mb-3 flex items-center gap-2 font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase"><span class="size-2.5 animate-pulse rounded-full bg-danger"></span>{t('ts.live_now')}</h2>
		<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{#each live as x (x.id)}<GameCard tie={x} path={tn.path} />{/each}</div>
	</section>
{/if}

<div class="grid items-start gap-5 lg:grid-cols-3">
	<div class="grid min-w-0 content-start gap-5 lg:col-span-2">
		<!-- the organiser's own words lead the page; without a description the games and standings come first -->
		{#if tn.description_html}
			<section class="rounded-2xl border border-line bg-white p-6 shadow-sm sm:p-8">
				<h2 class="mb-3 font-display text-xs font-extrabold tracking-[.14em] text-club uppercase">{t('ts.about')}</h2>
				<div class="prose max-w-none text-base">{@html tn.description_html}</div>
			</section>
		{/if}

		{#if finished}
			<Podium rows={tn.final_standings} path={tn.path} />
		{/if}

		{#if next.length}
			<section aria-labelledby="ts-next">
				<div class="mb-3 flex items-baseline justify-between gap-3">
					<h2 id="ts-next" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{t('tp.next_matches')}</h2>
					<a class="link text-sm" href="{base}/schedule">{t('tp.full_schedule')} →</a>
				</div>
				<div class="grid gap-3 sm:grid-cols-2">{#each next as x (x.id)}<GameCard tie={x} path={tn.path} />{/each}</div>
			</section>
		{/if}

		{#if recent.length}
			<section aria-labelledby="ts-recent">
				<h2 id="ts-recent" class="mb-3 font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{t('ts.recent')}</h2>
				<div class="grid gap-3 sm:grid-cols-2">{#each recent as x (x.id)}<GameCard tie={x} path={tn.path} />{/each}</div>
			</section>
		{/if}

		{#if tables.length}
			<section aria-labelledby="ts-tables">
				<div class="mb-3 flex items-baseline justify-between gap-3">
					<h2 id="ts-tables" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{t('tp.nav.standings')}</h2>
					<a class="link text-sm" href="{base}/standings">{t('ts.all_standings')} →</a>
				</div>
				<div class="grid gap-3 md:grid-cols-2">
					{#each tables as { s, tb } (s.stage.id + (tb.group?.id ?? ''))}
						<GroupTable title={tb.group ? `${s.stage.name} · ${tb.group.name}` : s.stage.name} table={tb} path={tn.path} />
					{/each}
				</div>
			</section>
		{/if}
	</div>

	<aside class="grid min-w-0 content-start gap-5">
		<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
			{#if mapSrc}<iframe title={t('ts.map')} src={mapSrc} class="h-52 w-full border-0" loading="lazy" referrerpolicy="no-referrer"></iframe>{/if}
			<div class="grid gap-4 p-5 text-sm">
				<div class="flex gap-3">
					<span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><CalendarDays size={18} aria-hidden="true" /></span>
					<div>
						<p class="font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.when')}</p>
						<ul class="mt-1 grid gap-0.5 text-ink">{#each tn.dates as d (d)}<li>{dayLabel(d, 'short')}</li>{/each}</ul>
					</div>
				</div>
				{#if tn.location}
					<div class="flex gap-3">
						<span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><MapPin size={18} aria-hidden="true" /></span>
						<div class="min-w-0">
							<p class="font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.where')}</p>
							<p class="mt-1 font-semibold text-ink">{tn.location}</p>
							{#if tn.location_address}<p class="text-muted">{tn.location_address}</p>{/if}
							{#if tn.maps_url}<a class="btn btn-outline btn-sm mt-2 gap-1.5" href={tn.maps_url} target="_blank" rel="noopener"><ExternalLink size={14} aria-hidden="true" /> {t('ts.open_map')}</a>{/if}
						</div>
					</div>
				{/if}
				{#if age}
					<div class="flex gap-3">
						<span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><Users size={18} aria-hidden="true" /></span>
						<div><p class="font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.category')}</p><p class="mt-1 text-ink">{age}</p></div>
					</div>
				{/if}
				{#if tn.contact_email}
					<div class="flex gap-3">
						<span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><Mail size={18} aria-hidden="true" /></span>
						<div class="min-w-0"><p class="font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.contact')}</p><a class="link mt-1 block truncate" href="mailto:{tn.contact_email}">{tn.contact_email}</a></div>
					</div>
				{/if}
			</div>
		</section>

		{#if tn.accepts_registrations}
			<a class="btn btn-primary btn-lg w-full font-display tracking-[.08em] uppercase" href="{base}/register">{t('tp.register_cta')}</a>
		{/if}

		{#if tn.rules_html || tn.rules_file_url}
			<section class="rounded-2xl border border-line bg-white p-5 shadow-sm" aria-labelledby="tp-rules">
				<h2 id="tp-rules" class="mb-2 flex items-center gap-2 font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase"><FileText size={15} class="shrink-0" aria-hidden="true" /> {t('tp.rules')}</h2>
				{#if tn.rules_html}<div class="prose max-w-none text-sm">{@html tn.rules_html}</div>{/if}
				{#if tn.rules_file_url}<a class="btn btn-outline mt-3 w-full gap-2" href={tn.rules_file_url} target="_blank" rel="noopener"><Download size={16} aria-hidden="true" /> {t('tp.rules_download')}</a>{/if}
			</section>
		{/if}

		{#if tn.sponsors.length}
			<section class="rounded-2xl border border-line bg-white p-5 shadow-sm" aria-labelledby="tp-sponsors">
				<h2 id="tp-sponsors" class="mb-4 font-display text-xs font-extrabold tracking-[.14em] text-muted uppercase">{t('tp.sponsors')}</h2>
				<ul class="grid grid-cols-2 items-center gap-x-4 gap-y-5">
					{#each tn.sponsors as sp (sp.name)}
						<li class="flex items-center justify-center">
							{#if sp.url}<a href={sp.url} target="_blank" rel="noopener sponsored" class="flex items-center" title={sp.name}>{@render mark(sp)}</a>{:else}{@render mark(sp)}{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	</aside>
</div>

{#snippet mark(sp: { name: string; logo_url: string | null })}
	{#if sp.logo_url}<img src={sp.logo_url} alt={sp.name} class="h-14 max-w-full object-contain" />{:else}<span class="text-center font-display text-sm font-bold text-ink">{sp.name}</span>{/if}
{/snippet}
