<script lang="ts">
	import { CalendarDays, Download, FileText, Lock, Mail, MapPin, Utensils, Wallet } from '@lucide/svelte';
	import MatchList from '#lib/components/tournaments/MatchList.svelte';
	import Standings from '#lib/components/tournaments/Standings.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { range } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const open = $derived(tn.status === 'draft' || tn.status === 'published');
	const hasTables = $derived(tn.standings.some((s) => s.tables.some((table) => table.rows.length)) || tn.brackets.some((b) => b.rounds.length));
</script>

<svelte:head>
	<title>{pageTitle(tn.name)}</title>
	{#if tn.status === 'draft'}<meta name="robots" content="noindex, nofollow" />{/if}
</svelte:head>

<section class="card card-body gap-4">
	<div class="flex flex-wrap items-center gap-2">
		<span class="badge {tn.status === 'in_progress' ? 'badge-success' : tn.status === 'cancelled' ? 'badge-error' : 'badge-info'}">{t(`tp.status.${tn.status}`)}</span>
		{#if tn.status === 'draft'}<span class="text-xs text-muted">{t('tp.draft_note')}</span>{/if}
	</div>
	<dl class="grid gap-3 text-sm sm:grid-cols-2">
		<div class="flex items-start gap-2"><CalendarDays size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><div><dt class="sr-only">{t('tp.when')}</dt><dd>{range(tn.start_date, tn.end_date)}</dd></div></div>
		{#if tn.location}<div class="flex items-start gap-2"><MapPin size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><div><dt class="sr-only">{t('tp.where')}</dt><dd>{tn.location}</dd></div></div>{/if}
		{#if tn.entry_fee_amount}
			<div class="flex items-start gap-2"><Wallet size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><div><dt class="sr-only">{t('tp.fee')}</dt><dd>{t('tp.fee_line', { fee: money(tn.entry_fee_amount) })}{#if tn.deposit_amount} · {t('tp.deposit_line', { deposit: money(tn.deposit_amount) })}{/if}</dd></div></div>
		{/if}
		{#if tn.lunch_mode !== 'none'}
			<div class="flex items-start gap-2"><Utensils size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><div><dt class="sr-only">{t('tp.lunch')}</dt><dd>{t(`tp.lunch.${tn.lunch_mode}`)}{#if tn.lunch_price_per_day} · {t('tp.lunch.per_day', { price: money(tn.lunch_price_per_day) })}{/if}</dd></div></div>
		{/if}
		{#if tn.earliest_birth_year !== null || tn.latest_birth_year !== null}
			<div class="text-muted">{#if tn.earliest_birth_year !== null && tn.latest_birth_year !== null}{t('tp.born.range', { earliest: tn.earliest_birth_year, latest: tn.latest_birth_year })}{:else if tn.earliest_birth_year !== null}{t('tp.born.from', { earliest: tn.earliest_birth_year })}{:else}{t('tp.born.until', { latest: tn.latest_birth_year ?? '' })}{/if}</div>
		{/if}
		{#if tn.contact_email}<div class="flex items-start gap-2"><Mail size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><div><dt class="sr-only">{t('tp.contact')}</dt><dd><a class="link" href="mailto:{tn.contact_email}">{tn.contact_email}</a></dd></div></div>{/if}
	</dl>
	{#if tn.description_html}<div class="prose max-w-none text-sm">{@html tn.description_html}</div>{/if}

	{#if tn.accepts_registrations}
		<a class="btn btn-primary w-full sm:w-auto sm:self-start" href="/tournaments/{tn.path}/register">{t('tp.register_cta')}</a>
	{:else if open}
		<p class="flex items-center gap-2 rounded-md bg-subhead px-4 py-3 text-sm text-muted" role="status"><Lock size={16} aria-hidden="true" /> {t('tp.closed.inline')}</p>
	{/if}
</section>

{#if tn.upcoming_matches.length}
	<section class="card overflow-hidden" aria-labelledby="tp-next">
		<h2 id="tp-next" class="px-4 pt-4 pb-1 font-display text-base font-bold text-ink">{t('tp.next_matches')}</h2>
		<MatchList matches={tn.upcoming_matches} slug={tn.path} />
		<div class="px-4 pb-3"><a class="link text-sm" href="/tournaments/{tn.path}/schedule">{t('tp.full_schedule')} →</a></div>
	</section>
{/if}

{#if hasTables}
	<Standings standings={tn.standings} brackets={tn.brackets} slug={tn.path} />
{/if}

{#if tn.rules_html || tn.rules_file_url}
	<section class="card card-body gap-3" aria-labelledby="tp-rules">
		<h2 id="tp-rules" class="flex items-center gap-2 font-display text-base font-bold text-ink"><FileText size={18} class="shrink-0" aria-hidden="true" /> {t('tp.rules')}</h2>
		{#if tn.rules_html}<div class="prose max-w-none text-sm">{@html tn.rules_html}</div>{/if}
		{#if tn.rules_file_url}
			<a class="btn btn-outline w-full gap-2 sm:w-auto sm:self-start" href={tn.rules_file_url} target="_blank" rel="noopener"><Download size={16} aria-hidden="true" /> {t('tp.rules_download')}</a>
		{/if}
	</section>
{/if}

{#if tn.sponsors.length}
	<section class="card card-body gap-3" aria-labelledby="tp-sponsors">
		<h2 id="tp-sponsors" class="font-display text-base font-bold text-ink">{t('tp.sponsors')}</h2>
		<ul class="flex flex-wrap items-center gap-x-8 gap-y-5">
			{#each tn.sponsors as sp (sp.name)}
				<li>
					{#if sp.url}<a href={sp.url} target="_blank" rel="noopener sponsored" class="flex items-center" title={sp.name}>{@render mark(sp)}</a>{:else}{@render mark(sp)}{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

{#snippet mark(sp: { name: string; logo_url: string | null })}
	{#if sp.logo_url}<img src={sp.logo_url} alt={sp.name} class="h-14 max-w-40 object-contain" />{:else}<span class="font-display text-sm font-bold text-ink">{sp.name}</span>{/if}
{/snippet}
