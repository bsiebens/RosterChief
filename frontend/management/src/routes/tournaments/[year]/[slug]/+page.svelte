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

<!-- hero: status, the facts as tiles and the way to register -->
<section class="overflow-hidden rounded-2xl border border-line bg-white shadow-sm">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-subhead px-5 py-3">
		<div class="flex flex-wrap items-center gap-2">
			<span class="badge {tn.status === 'in_progress' ? 'badge-success' : tn.status === 'cancelled' ? 'badge-error' : 'badge-info'}">{t(`tp.status.${tn.status}`)}</span>
			{#if tn.status === 'draft'}<span class="text-xs text-muted">{t('tp.draft_note')}</span>{/if}
		</div>
		{#if tn.accepts_registrations}
			<a class="btn btn-primary btn-sm" href="/tournaments/{tn.path}/register">{t('tp.register_cta')}</a>
		{:else if open}
			<p class="flex items-center gap-2 text-xs text-muted" role="status"><Lock size={14} aria-hidden="true" /> {t('tp.closed.inline')}</p>
		{/if}
	</div>
	<dl class="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-4">
		<div class="flex items-start gap-3 bg-white p-4"><span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><CalendarDays size={18} aria-hidden="true" /></span><div class="min-w-0"><dt class="font-mono text-[10px] tracking-[.1em] text-muted uppercase">{t('tp.when')}</dt><dd class="text-sm font-semibold text-ink">{range(tn.start_date, tn.end_date)}</dd></div></div>
		<div class="flex items-start gap-3 bg-white p-4"><span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><MapPin size={18} aria-hidden="true" /></span><div class="min-w-0"><dt class="font-mono text-[10px] tracking-[.1em] text-muted uppercase">{t('tp.where')}</dt><dd class="text-sm font-semibold text-ink">{tn.location ?? '—'}</dd></div></div>
		<div class="flex items-start gap-3 bg-white p-4"><span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><Wallet size={18} aria-hidden="true" /></span><div class="min-w-0"><dt class="font-mono text-[10px] tracking-[.1em] text-muted uppercase">{t('tp.fee')}</dt><dd class="text-sm font-semibold text-ink">{#if tn.entry_fee_amount}{t('tp.fee_line', { fee: money(tn.entry_fee_amount) })}{#if tn.deposit_amount}<span class="block text-xs font-normal text-muted">{t('tp.deposit_line', { deposit: money(tn.deposit_amount) })}</span>{/if}{:else}—{/if}</dd></div></div>
		<div class="flex items-start gap-3 bg-white p-4"><span class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-club/10 text-club"><Utensils size={18} aria-hidden="true" /></span><div class="min-w-0"><dt class="font-mono text-[10px] tracking-[.1em] text-muted uppercase">{t('tp.lunch')}</dt><dd class="text-sm font-semibold text-ink">{#if tn.lunch_mode !== 'none'}{t(`tp.lunch.${tn.lunch_mode}`)}{#if tn.lunch_price_per_day}<span class="block text-xs font-normal text-muted">{t('tp.lunch.per_day', { price: money(tn.lunch_price_per_day) })}</span>{/if}{:else}—{/if}</dd></div></div>
	</dl>
	{#if tn.earliest_birth_year !== null || tn.latest_birth_year !== null || tn.contact_email}
		<div class="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-line px-5 py-3 text-sm">
			{#if tn.earliest_birth_year !== null || tn.latest_birth_year !== null}
				<span class="text-muted">{#if tn.earliest_birth_year !== null && tn.latest_birth_year !== null}{t('tp.born.range', { earliest: tn.earliest_birth_year, latest: tn.latest_birth_year })}{:else if tn.earliest_birth_year !== null}{t('tp.born.from', { earliest: tn.earliest_birth_year })}{:else}{t('tp.born.until', { latest: tn.latest_birth_year ?? '' })}{/if}</span>
			{/if}
			{#if tn.contact_email}<span class="flex items-center gap-2"><Mail size={15} class="text-muted" aria-hidden="true" /><a class="link" href="mailto:{tn.contact_email}">{tn.contact_email}</a></span>{/if}
		</div>
	{/if}
</section>

<div class="grid items-start gap-4 lg:grid-cols-3">
	<div class="grid min-w-0 content-start gap-4 lg:col-span-2">
		{#if tn.upcoming_matches.length}
			<section class="card overflow-hidden" aria-labelledby="tp-next">
				<h2 id="tp-next" class="border-b border-line px-4 py-3 font-display text-base font-bold text-ink">{t('tp.next_matches')}</h2>
				<MatchList matches={tn.upcoming_matches} slug={tn.path} />
				<div class="border-t border-line px-4 py-3"><a class="link text-sm" href="/tournaments/{tn.path}/schedule">{t('tp.full_schedule')} →</a></div>
			</section>
		{/if}
		{#if hasTables}
			<Standings standings={tn.standings} brackets={tn.brackets} slug={tn.path} />
		{/if}
	</div>
	<aside class="grid min-w-0 content-start gap-4">
		{#if tn.description_html}
			<section class="card card-body gap-2"><div class="prose max-w-none text-sm">{@html tn.description_html}</div></section>
		{/if}
		{#if tn.rules_html || tn.rules_file_url}
			<section class="card card-body gap-3" aria-labelledby="tp-rules">
				<h2 id="tp-rules" class="flex items-center gap-2 font-display text-base font-bold text-ink"><FileText size={18} class="shrink-0" aria-hidden="true" /> {t('tp.rules')}</h2>
				{#if tn.rules_html}<div class="prose max-w-none text-sm">{@html tn.rules_html}</div>{/if}
				{#if tn.rules_file_url}
					<a class="btn btn-outline w-full gap-2" href={tn.rules_file_url} target="_blank" rel="noopener"><Download size={16} aria-hidden="true" /> {t('tp.rules_download')}</a>
				{/if}
			</section>
		{/if}
		{#if tn.sponsors.length}
			<section class="card card-body gap-3" aria-labelledby="tp-sponsors">
				<h2 id="tp-sponsors" class="font-display text-base font-bold text-ink">{t('tp.sponsors')}</h2>
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
