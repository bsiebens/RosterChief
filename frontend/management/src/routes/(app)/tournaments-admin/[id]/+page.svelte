<script lang="ts">
	import { untrack } from 'svelte';
	import { CalendarClock, ClipboardList, Save, Trophy, Wallet } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import TournamentFields from '#lib/components/tournaments/TournamentFields.svelte';
	import TournamentSummary from '#lib/components/tournaments/TournamentSummary.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner } from '#lib/tournaments.svelte.ts';
	import { formOf, payload } from '#lib/tournaments-form.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const run = new Runner();
	// svelte-ignore state_referenced_locally
	let v = $state(formOf(data.tournament));
	$effect(() => {
		const stored = data.tournament;
		untrack(() => (v = formOf(stored)));
	});

	const save = (e: SubmitEvent) => {
		e.preventDefault();
		// The home team is set when the tournament is created; changing it afterwards is left to the contract's PATCH only when it differs.
		const body = payload(v, { homeTeam: v.home_team_id !== (tn.home_team?.id ?? '') });
		return run.patch(`/tournaments/${tn.id}`, body, undefined, { notice: t('tn.saved') });
	};

	usePageHeader(() => ({ title: tn.name, context: t('tn.tab.settings') }));
</script>

<svelte:head><title>{pageTitle(tn.name)}</title></svelte:head>

<form class="grid items-start gap-5 xl:grid-cols-3" onsubmit={save}>
	<div class="grid content-start gap-5 xl:col-span-2">
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Trophy size={16} aria-hidden="true" />{t('tn.card.details')}</h2>
			{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
			<TournamentFields bind:v errors={run.fields} teams={data.teams} part="basics" />
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
				<button class="btn btn-primary w-full gap-2" type="submit" disabled={run.busy}><Save size={16} /> {t('common.save')}</button>
			</div>
		</section>
	</aside>
</form>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
