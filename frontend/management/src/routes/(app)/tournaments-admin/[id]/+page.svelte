<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { ArrowRight, CalendarClock, CalendarDays, Check, ClipboardList, Copy, ExternalLink, Link2, Save, Trash2, Trophy, Wallet } from '@lucide/svelte';
	import { page } from '$app/state';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import TournamentFields from '#lib/components/tournaments/TournamentFields.svelte';
	import TournamentSummary from '#lib/components/tournaments/TournamentSummary.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner, TBASE } from '#lib/tournaments.svelte.ts';
	import { formOf, payload } from '#lib/tournaments-form.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	/** The address of the public pages on this club's site (the management app runs on the same host). */
	const publicUrl = $derived(`${page.url.origin}${tn.public_path}`);
	let copied = $state(false);
	async function copyLink() {
		try {
			await navigator.clipboard.writeText(publicUrl);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// Clipboard not available: the link is shown and can be selected by hand.
		}
	}
	const run = new Runner();
	/** The next step of a tournament's life; cancelling stays a deliberate choice in the status buttons. */
	const NEXT: Record<string, string> = { draft: 'published', published: 'in_progress', in_progress: 'completed' };
	const next = $derived(NEXT[tn.status] ?? null);
	const advance = () => next && run.patch(`/tournaments/${tn.id}`, { status: next }, undefined, { notice: t('tn.next_status_done', { status: t(`tn.status.${next}`) }) });
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

	let deleteOpen = $state(false);
	const remove = async () => {
		const ok = await run.del(`/tournaments/${tn.id}`, undefined, { reload: false });
		if (ok) await goto(TBASE);
	};

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.settings') }));
</script>

<svelte:head><title>{pageTitle(tn.name)}</title></svelte:head>

<form class="grid items-start gap-5 xl:grid-cols-3" onsubmit={save}>
	<div class="grid content-start gap-5 xl:col-span-2">
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Trophy size={16} aria-hidden="true" />{t('tn.card.details')}</h2>
			<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
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
				{#if next}
					<button class="btn btn-outline w-full justify-between gap-2" type="button" disabled={run.busy} onclick={advance}><span class="flex items-center gap-2"><ArrowRight size={16} aria-hidden="true" /> {t('tn.next_status')}</span><span class="badge badge-info badge-sm">{t(`tn.status.${next}`)}</span></button>
				{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" disabled={run.busy}><Save size={16} /> {t('common.save')}</button>
				{#if tn.status === 'draft'}
					<ActionList><ActionRow icon={Trash2} danger onclick={() => { run.clear(); deleteOpen = true; }}>{t('tn.delete')}</ActionRow></ActionList>
				{/if}
			</div>
		</section>
		<section class="card card-body card-hl gap-2" aria-labelledby="tn-pub">
			<h2 id="tn-pub" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Link2 size={16} class="shrink-0" aria-hidden="true" /> {t('tn.card.public')}</h2>
			<div class="flex items-center gap-2">
				<input class="input input-bordered input-sm min-w-0 flex-1 font-mono text-xs" readonly value={publicUrl} aria-label={t('tn.card.public')} onfocus={(e) => e.currentTarget.select()} />
				<button class="btn btn-outline btn-sm gap-1" type="button" onclick={copyLink}>{#if copied}<Check size={14} /> {t('tn.public.copied')}{:else}<Copy size={14} /> {t('tn.public.copy')}{/if}</button>
				<a class="btn btn-outline btn-sm gap-1" href={publicUrl} target="_blank" rel="noopener"><ExternalLink size={14} /> {t('tn.public.open')}</a>
			</div>
			<p class="text-xs text-muted">{t('tn.public.help')}</p>
		</section>
	</aside>
</form>

<Modal bind:open={deleteOpen} title={t('tn.delete_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.delete_body', { name: tn.name })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
