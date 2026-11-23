<script lang="ts">
	import { goto } from '$app/navigation';
	import { CalendarClock, CalendarDays, ClipboardList, Save, Trophy, Wallet } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import TournamentFields from '#lib/components/tournaments/TournamentFields.svelte';
	import TournamentSummary from '#lib/components/tournaments/TournamentSummary.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner, TBASE, type TournamentDetail } from '#lib/tournaments.svelte.ts';
	import { formOf, payload } from '#lib/tournaments-form.ts';

	let { data } = $props();
	const run = new Runner();
	let v = $state(formOf());

	async function create(e: SubmitEvent) {
		e.preventDefault();
		let id = '';
		const ok = await run.run(() => api<TournamentDetail>('POST', '/tournaments', { body: payload(v) }), (d) => void (id = d?.id ?? ''), { reload: false });
		if (ok && id) await goto(`${TBASE}/${id}`);
	}

	usePageHeader(() => ({ title: t('tn.add'), context: t('tn.title') }));
</script>

<svelte:head><title>{pageTitle(t('tn.add'))}</title></svelte:head>

<form class="grid items-start gap-5 xl:grid-cols-3" onsubmit={create}>
	<div class="grid content-start gap-5 xl:col-span-2">
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Trophy size={16} aria-hidden="true" />{t('tn.card.details')}</h2>
			{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
			<TournamentFields bind:v errors={run.fields} teams={data.teams} part="basics" />
		</section>
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><CalendarDays size={16} aria-hidden="true" />{t('tn.card.days')}</h2>
			<TournamentFields bind:v errors={run.fields} teams={data.teams} part="days" />
		</section>
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><CalendarClock size={16} aria-hidden="true" />{t('tn.card.entries')}</h2>
			<TournamentFields bind:v errors={run.fields} teams={data.teams} part="entries" />
		</section>
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Wallet size={16} aria-hidden="true" />{t('tn.card.fees')}</h2>
			<TournamentFields bind:v errors={run.fields} teams={data.teams} part="fees" />
		</section>
	</div>
	<aside class="grid content-start gap-5 xl:sticky xl:top-20">
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" />{t('tn.card.summary')}</h2>
			<TournamentSummary {v} teams={data.teams} />
			<div class="grid gap-2">
				<button class="btn btn-primary w-full gap-2" type="submit" disabled={run.busy}><Save size={16} /> {t('tn.create')}</button>
				<a class="btn btn-outline w-full" href={TBASE}>{t('common.cancel')}</a>
			</div>
		</section>
	</aside>
</form>
