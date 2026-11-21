<script lang="ts">
	import { CalendarDays, ChevronRight, MapPin, Trophy } from '@lucide/svelte';
	import PublicShell from '#lib/components/registration/PublicShell.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { range, type TournamentItem } from '#lib/tournaments-public.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const club = $derived(data.mode === 'club' ? data.branding : null);
</script>

<svelte:head><title>{pageTitle(t('tp.list.title'))}</title></svelte:head>

{#snippet row(item: TournamentItem)}
	<a class="card card-body flex-row items-center gap-3 hover:border-ink" href="/tournaments/{item.slug}">
		<span class="grid size-11 shrink-0 place-items-center rounded-box bg-subhead text-ink"><Trophy size={20} aria-hidden="true" /></span>
		<span class="min-w-0 flex-1">
			<b class="block truncate font-display text-lg text-ink">{item.name}</b>
			<span class="mt-0.5 flex flex-wrap items-center gap-x-3 text-sm text-muted">
				<span class="inline-flex items-center gap-1"><CalendarDays size={14} aria-hidden="true" /> {range(item.start_date, item.end_date)}</span>
				{#if item.location}<span class="inline-flex items-center gap-1"><MapPin size={14} aria-hidden="true" /> {item.location}</span>{/if}
			</span>
		</span>
		{#if item.status === 'in_progress'}<span class="badge badge-success">{t('tp.status.in_progress')}</span>
		{:else if item.accepts_registrations}<span class="badge badge-info">{t('tp.registration_open')}</span>{/if}
		<ChevronRight size={18} class="shrink-0 text-muted" aria-hidden="true" />
	</a>
{/snippet}

<PublicShell title={t('tp.list.title')} kicker={t('tp.kicker')} {club}>
	{#if !data.list.upcoming.length && !data.list.past.length}
		<div class="card card-body items-center gap-2 py-10 text-center" role="status">
			<Trophy size={32} class="text-dim" aria-hidden="true" />
			<p class="font-display text-xl font-bold text-ink">{t('tp.list.empty')}</p>
		</div>
	{/if}
	{#if data.list.upcoming.length}
		<section class="grid gap-3" aria-labelledby="tp-upcoming">
			<h2 id="tp-upcoming" class="font-display text-sm font-bold tracking-[.12em] text-muted uppercase">{t('tp.list.upcoming')}</h2>
			{#each data.list.upcoming as item (item.slug)}{@render row(item)}{/each}
		</section>
	{/if}
	{#if data.list.past.length}
		<section class="grid gap-3" aria-labelledby="tp-past">
			<h2 id="tp-past" class="font-display text-sm font-bold tracking-[.12em] text-muted uppercase">{t('tp.list.past')}</h2>
			{#each data.list.past as item (item.slug)}{@render row(item)}{/each}
		</section>
	{/if}
</PublicShell>
