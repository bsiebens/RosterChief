<script lang="ts">
	import { Check, ClipboardPen, Plus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, numOrNull, Runner, TBASE, type MatchDoc } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const run = new Runner();

	let open = $state(false);
	let m = $state<MatchDoc | null>(null);
	let f = $state({ home: '', away: '', ot: false, ph: '', pa: '' });
	function openResult(match: MatchDoc) {
		m = match;
		f = { home: match.score_home === null ? '' : String(match.score_home), away: match.score_away === null ? '' : String(match.score_away), ot: match.went_to_overtime, ph: match.penalty_home === null ? '' : String(match.penalty_home), pa: match.penalty_away === null ? '' : String(match.penalty_away) };
		run.clear();
		open = true;
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!m) return;
		const body = { score_home: Number(f.home), score_away: Number(f.away), went_to_overtime: f.ot, penalty_home: numOrNull(f.ph), penalty_away: numOrNull(f.pa) };
		if (await run.post<{ updated_match_ids: string[] }>(`/tournaments/matches/${m.id}/result`, body, undefined, { notice: t('tn.scores.saved') })) open = false;
	}
	const nextRound = (stageId: string) => run.post<{ generated_count: number }>(`/tournaments/${tn.id}/stages/${stageId}/swiss/next-round`, {}, (d) => void (run.notice = t('tn.scores.generated', { n: d?.generated_count ?? 0 })), { notice: '' });
	const done = (x: MatchDoc) => x.score_home !== null && x.score_away !== null;

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.scores') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.scores'))}</title></svelte:head>

<div class="grid gap-4">
	{#if run.error && !open}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error || !data.scores}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#each data.scores.stages as st (st.stage.id)}
			<section class="card card-body gap-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardPen size={16} class="shrink-0" aria-hidden="true" /> {st.stage.name}</h2>
					<div class="flex flex-wrap gap-2">
						{#if st.ready_for_next_phase}<a class="btn btn-primary btn-sm gap-1" href="{TBASE}/{tn.id}/stages/{st.stage.id}/next-phase"><Check size={14} /> {t('tn.scores.review_next')}</a>{/if}
						{#if canOperate && st.swiss_ready_for_next_round}<button class="btn btn-primary btn-sm gap-1" type="button" disabled={run.busy} onclick={() => nextRound(st.stage.id)}><Plus size={14} /> {t('tn.scores.swiss_next')}</button>{/if}
					</div>
				</div>
				{#each st.rounds as r (r.round_number)}
					<div class="grid gap-1">
						<h3 class="flex items-center gap-2 text-xs font-bold text-muted">{r.label}{#if r.all_complete}<span class="badge badge-success badge-xs">{t('tn.scores.complete')}</span>{/if}</h3>
						<div class="overflow-x-auto">
							<table class="table table-sm">
								<tbody>
									{#each r.matches as x (x.id)}
										<tr class="hover:bg-paper">
											<td class="w-36 font-mono text-xs whitespace-nowrap">{x.scheduled_start ? fmtDateTime(x.scheduled_start) : '—'}</td>
											<td class="hidden w-24 text-xs sm:table-cell">{x.field?.name ?? ''}</td>
											<td class="text-right text-sm">{x.home_display}</td>
											<td class="w-24 text-center font-mono text-sm font-bold whitespace-nowrap">
												{#if done(x)}{x.score_home} – {x.score_away}{#if x.went_to_overtime} <span class="text-xs font-normal text-muted">{t('tn.scores.ot')}</span>{/if}{#if x.penalty_home !== null} <span class="text-xs font-normal text-muted">({x.penalty_home}–{x.penalty_away})</span>{/if}{:else}<span class="text-muted">vs</span>{/if}
											</td>
											<td class="text-sm">{x.away_display}</td>
											<td class="w-24 text-right"><div class="flex items-center justify-end gap-1">
												{#if canOperate}<button class="btn btn-outline btn-xs" type="button" disabled={!x.home_team || !x.away_team} onclick={() => openResult(x)}>{done(x) ? t('common.edit') : t('tn.scores.enter')}</button>{/if}
											</div></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.sched.no_matches')}</p>
				{/each}
			</section>
		{:else}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('tn.scores.empty')}</p></div>
		{/each}
	{/if}
</div>

<Modal bind:open title={t('tn.scores.enter')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p class="text-sm font-semibold">{m?.home_display} – {m?.away_display}</p>
		<div class="grid grid-cols-2 gap-3">
			<label class="form-control"><span class="label-text mb-1">{m?.home_display}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.home} /></label>
			<label class="form-control"><span class="label-text mb-1">{m?.away_display}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.away} /></label>
		</div>
		{#each ['score_home', 'score_away'] as k (k)}{#if run.fields[k]}<span class="text-xs text-error">{run.fields[k]}</span>{/if}{/each}
		<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3"><input type="checkbox" class="checkbox" bind:checked={f.ot} /><span class="text-sm">{t('tn.scores.overtime')}</span></label>
		<div class="grid grid-cols-2 gap-3">
			<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_home')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={f.ph} /></label>
			<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_away')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={f.pa} /></label>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
