<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { ArrowLeft, ArrowRight, ChartLine, Check, ClipboardCheck, FileDown, ListChecks, NotebookPen, Play, Send, Square, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import AttendancePanel from '#lib/components/evaluations/AttendancePanel.svelte';
	import TrendChart from '#lib/components/evaluations/TrendChart.svelte';
	import { bySection } from '#lib/forms.ts';
	import { answerShown, evaluateHref, outcomesPdfHref } from '#lib/evaluations.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const active = $derived(data.meeting.active);
	const latest = $derived(data.meeting.latest);
	const decided = $derived(new Map(data.meeting.outcomes.map((o) => [o.player.id, o.decision])));
	const index = $derived(data.players.findIndex((p) => p.id === data.selected));
	const prev = $derived(index > 0 ? data.players[index - 1] : null);
	const next = $derived(index >= 0 && index < data.players.length - 1 ? data.players[index + 1] : null);
	const review = $derived(data.review);
	const groups = $derived(review ? bySection(review.questions.filter((q) => q.entries.length).map((q) => ({ section: q.field.section, q }))) : []);
	const own = $derived(data.selected ? (decided.get(data.selected) ?? '') : '');
	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { dateStyle: 'medium' }).format(new Date(iso));
	const stamp = (iso: string) => new Intl.DateTimeFormat(getLang(), { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
	const to = (player: string, since = data.since) => {
		const sp = new URLSearchParams();
		if (player) sp.set('player', player);
		if (since) sp.set('since', since);
		return `?${sp}`;
	};
	const err = $derived(form && 'message' in form && form.message ? String(form.message) : '');
	let busy = $state(false);
	const submit = () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: true });
			busy = false;
		};
	};

	usePageHeader(() => ({ title: t('eval.meeting.title'), context: data.checklist.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/evaluations"><ArrowLeft size={16} /> {t('eval.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eval.meeting.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if err}<Alert kind="error">{err}</Alert>{/if}

	<section class="card card-body flex-row flex-wrap items-center gap-3" aria-label={t('eval.meeting.status')}>
		{#if active}
			<span class="badge badge-success gap-1"><span class="size-2 rounded-full bg-current"></span> {t('eval.meeting.open')}</span>
			<span class="text-sm text-muted">{t('eval.meeting.since', { when: stamp(active.started_at) })}{active.started_by ? ` · ${active.started_by.name}` : ''}</span>
			<span class="text-sm text-muted">· {t('eval.meeting.decided', { n: data.meeting.outcomes.length })}</span>
			<div class="ml-auto flex flex-wrap gap-2">
				<a class="btn btn-outline btn-sm gap-1" href={outcomesPdfHref(active.id)} target="_blank" rel="noopener"><FileDown size={14} /> {t('eval.meeting.pdf')}</a>
				<form method="POST" action="?/end" use:enhance={submit}>
					<input type="hidden" name="session_id" value={active.id} />
					<button class="btn btn-outline btn-error btn-sm gap-1" type="submit" disabled={busy}><Square size={14} /> {t('eval.meeting.end')}</button>
				</form>
			</div>
		{:else}
			<p class="text-sm text-muted">{latest ? t('eval.meeting.last', { when: stamp(latest.ended_at ?? latest.started_at), n: data.meeting.outcomes.length }) : t('eval.meeting.none')}</p>
			<div class="ml-auto flex flex-wrap gap-2">
				{#if latest}<a class="btn btn-outline btn-sm gap-1" href={outcomesPdfHref(latest.id)} target="_blank" rel="noopener"><FileDown size={14} /> {t('eval.meeting.pdf_last')}</a>{/if}
				<form method="POST" action="?/start" use:enhance={submit}>
					<button class="btn btn-primary btn-sm gap-1" type="submit" disabled={busy}><Play size={14} /> {t('eval.meeting.start')}</button>
				</form>
			</div>
		{/if}
	</section>

	{#if form && 'shared' in form}<Alert kind="success">{t('eval.release.done', { n: form.shared ?? 0 })}</Alert>{/if}
	{#if !active && data.unreleased > 0}
		<section class="card card-body gap-3" aria-labelledby="release-title">
			<h2 id="release-title" class="card-title"><Send size={16} aria-hidden="true" /> {t('eval.release.title')}</h2>
			<p class="text-sm text-muted">{t('eval.release.help', { n: data.unreleased })}</p>
			<form class="flex flex-wrap items-end gap-2" method="POST" action="?/release" use:enhance={submit}>
				<label class="grid gap-1 text-sm">{t('eval.share.level')}
					<select class="select select-bordered select-sm" name="release">
						<option value="notes">{t('eval.share.notes')}</option>
						<option value="full">{t('eval.share.full')}</option>
					</select>
				</label>
				<button class="btn btn-primary btn-sm gap-1" type="submit" disabled={busy}><Send size={14} /> {t('eval.release.go', { n: data.unreleased })}</button>
			</form>
			<p class="text-xs text-muted">{t('eval.release.families')}</p>
		</section>
	{/if}

	{#if !data.players.length && !data.since}
		<div class="card card-body items-center py-10 text-center text-muted"><p>{t('eval.meeting.empty')}</p></div>
	{:else}
		<div class="grid items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
			<aside class="card card-body gap-3 lg:sticky lg:top-20" aria-label={t('eval.meeting.players')}>
				<form
					class="flex items-end gap-2"
					onsubmit={(e) => {
						e.preventDefault();
						goto(to('', String(new FormData(e.currentTarget).get('since') ?? '')), { reset: false });
					}}>
					<label class="form-control min-w-0 flex-1">
						<span class="label-text text-xs">{t('eval.meeting.since_filter')}</span>
						<input class="input input-bordered input-sm w-full" type="date" name="since" value={data.since} />
					</label>
					<button class="btn btn-outline btn-sm" type="submit">{t('eval.meeting.filter')}</button>
				</form>
				{#if data.since}<a class="link text-xs" href={to('', '')}>{t('common.clear_filters')}</a>{/if}
				<ul class="grid max-h-[60vh] gap-0.5 overflow-y-auto">
					{#each data.players as p (p.id)}
						<li>
							<a href={to(p.id)} aria-current={p.id === data.selected ? 'true' : undefined} data-sveltekit-noscroll class="flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-sm {p.id === data.selected ? 'bg-primary text-primary-content' : 'hover:bg-surface'}">
								<span class="truncate">{p.name}</span>
								{#if decided.has(p.id)}<Check size={14} aria-label={t('eval.meeting.has_decision')} />{/if}
							</a>
						</li>
					{:else}
						<li class="p-2 text-sm text-muted">{t('eval.meeting.none_since')}</li>
					{/each}
				</ul>
			</aside>

			{#if review}
				<div class="grid min-w-0 gap-4">
					<section class="card card-body gap-3">
						<div class="flex flex-wrap items-start justify-between gap-3">
							<div class="grid gap-1">
								<h2 class="font-display text-2xl font-extrabold tracking-wide text-ink uppercase"><a class="hover:underline" href="/members/{review.player.id}?tab=evaluations">{review.player.name}</a></h2>
								<p class="text-sm text-muted">{review.teams.length ? review.teams.map((x) => x.name).join(', ') : t('eval.meeting.no_team')}</p>
							</div>
							<div class="flex flex-wrap gap-2">
								{#if data.checklist.is_active && data.checklist.criteria_count}<a class="btn btn-outline btn-sm gap-1" href={evaluateHref(data.checklist.id, review.player.id)}><ClipboardCheck size={14} /> {t('eval.meeting.new_evaluation')}</a>{/if}
							</div>
						</div>
						<AttendancePanel att={review.attendance} />
						<div class="flex justify-between gap-2 border-t border-rule pt-3 text-sm">
							{#if prev}<a class="btn btn-ghost btn-sm gap-1" href={to(prev.id)} data-sveltekit-noscroll><ArrowLeft size={14} /> {prev.name}</a>{:else}<span></span>{/if}
							<span class="self-center font-mono text-xs text-muted">{index + 1} / {data.players.length}</span>
							{#if next}<a class="btn btn-ghost btn-sm gap-1" href={to(next.id)} data-sveltekit-noscroll>{next.name} <ArrowRight size={14} /></a>{:else}<span></span>{/if}
						</div>
					</section>

					{#each groups as g, gi (gi)}
						<section class="grid gap-3">
							{#if g.section}<h3 class="flex items-center gap-2 border-b border-rule pb-1 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListChecks size={16} aria-hidden="true" /> {g.section}</h3>{/if}
							<div class="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))]">
								{#each g.items as { q } (q.field.id)}
									<section class="card card-body gap-2 p-4">
										<h4 class="flex flex-wrap items-center gap-2 font-semibold text-ink"><ChartLine size={16} aria-hidden="true" /> {q.field.label}{#if !q.field.is_active}<span class="badge badge-sm badge-warning">{t('eval.meeting.removed')}</span>{/if}</h4>
										{#if q.trend.length}
											<TrendChart label={q.field.label} points={q.trend.map((p) => ({ id: p.evaluation_id, label: day(p.at), value: p.value }))} />
										{:else}
											<ul class="grid gap-1.5">
												{#each q.entries as e (e.evaluation_id)}
													<li class="flex gap-3 text-sm"><span class="w-24 shrink-0 font-mono text-xs text-muted">{day(e.at)}</span><span>{answerShown({ field_type: q.field.field_type, options: q.field.options, value: e.value }, t('common.yes'), t('common.no'))}</span></li>
												{/each}
											</ul>
										{/if}
									</section>
								{/each}
							</div>
						</section>
					{:else}
						<div class="card card-body text-center text-muted">{t('eval.meeting.no_scores')}</div>
					{/each}

					<section class="card card-body gap-3">
						<h3 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><NotebookPen size={16} class="shrink-0" aria-hidden="true" /> {t('eval.notes.title')}</h3>
						<form method="POST" action="?/note" class="grid gap-2" use:enhance={submit}>
							<input type="hidden" name="player_id" value={review.player.id} />
							<label class="sr-only" for="note">{t('eval.notes.label')}</label>
							<textarea id="note" name="note" class="textarea textarea-bordered w-full" rows="2" placeholder={t('eval.notes.placeholder')} required></textarea>
							<div><button class="btn btn-outline btn-sm" type="submit" disabled={busy}>{t('eval.notes.add')}</button></div>
						</form>
						<ul class="grid gap-2">
							{#each review.notes as n (n.id)}
								<li class="flex items-start justify-between gap-2 rounded-lg bg-surface p-3 text-sm">
									<div>
										<p class="whitespace-pre-line text-slate">{n.note}</p>
										<p class="mt-1 font-mono text-xs text-muted">{n.author?.name ?? t('eval.notes.unknown')} · {stamp(n.created)}</p>
									</div>
									<form method="POST" action="?/deleteNote" use:enhance={submit}>
										<input type="hidden" name="note_id" value={n.id} />
										<button class="btn btn-ghost btn-xs btn-square" type="submit" aria-label={t('eval.notes.delete')}><Trash2 size={13} /></button>
									</form>
								</li>
							{/each}
						</ul>
					</section>

					{#if active}
						<section class="card card-body gap-3">
							<h3 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardCheck size={16} class="shrink-0" aria-hidden="true" /> {t('eval.outcome.title')}</h3>
							<p class="text-xs text-muted">{t('eval.outcome.help')}</p>
							{#key review.player.id}
								<form method="POST" action="?/outcome" class="grid gap-2" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; }; }}>
									<input type="hidden" name="session_id" value={active.id} />
									<input type="hidden" name="player_id" value={review.player.id} />
									<label class="sr-only" for="decision">{t('eval.outcome.title')}</label>
									<textarea id="decision" name="decision" class="textarea textarea-bordered w-full" rows="2" placeholder={t('eval.outcome.placeholder')}>{own}</textarea>
									<div class="flex items-center gap-3">
										<button class="btn btn-primary btn-sm" type="submit" disabled={busy}>{t('eval.outcome.save')}</button>
										{#if form && 'done' in form && form.done === 'outcome'}<span role="status" class="badge badge-success h-8 gap-1.5 px-3 font-semibold"><Check size={14} aria-hidden="true" /> {t('eval.outcome.saved')}</span>{/if}
									</div>
								</form>
							{/key}
						</section>
					{/if}
				</div>
			{:else}
				<div class="card card-body items-center py-10 text-center text-muted"><p>{t('eval.meeting.pick')}</p></div>
			{/if}
		</div>
	{/if}
</div>
