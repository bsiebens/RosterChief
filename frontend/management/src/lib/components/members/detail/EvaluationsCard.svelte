<script lang="ts">
	import { enhance } from '$app/forms';
	import { TrendingUp, ClipboardCheck, Eye, Lock, Star, Trash2 } from '@lucide/svelte';
	import TrendChart from '#lib/components/evaluations/TrendChart.svelte';
	import { answerShown, evaluateHref, fmt, num, overall, type ChecklistItem, type EvaluationOut } from '#lib/evaluations.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';

	/**
	 * A member's evaluations, newest first, from every checklist: the latest scores, the average over time and the timeline with every
	 * answer. Only rendered for someone with the execute permission (the API refuses anyone else). `canManage` may delete any evaluation,
	 * everyone else only the ones they gave themselves.
	 */
	let { evaluations, checklists, memberId, myMemberId = null, canManage = false, error = false }: { evaluations: EvaluationOut[]; checklists: ChecklistItem[]; memberId: string; myMemberId?: string | null; canManage?: boolean; error?: boolean } = $props();

	const latest = $derived(evaluations[0]);
	const numeric = $derived(latest ? latest.answers.filter((a) => a.field_type === 'number' && num(a.value) !== null) : []);
	const points = $derived(
		[...evaluations]
			.reverse()
			.map((e) => ({ id: e.id, label: formatDate(e.submitted_at).replace(/,? \d{4}$/, ''), value: overall(e) }))
			.filter((p): p is { id: string; label: string; value: number } => p.value !== null)
	);
	const usable = $derived(checklists.filter((c) => c.is_active && c.criteria_count > 0));
	const mayDelete = (e: EvaluationOut) => canManage || (!!myMemberId && e.evaluator?.id === myMemberId);
</script>

<div class="grid items-start gap-4 {latest || points.length ? 'lg:grid-cols-[22rem_1fr]' : ''}">
	{#if latest || points.length}
	<div class="grid content-start gap-4">
		{#if latest}
			<div class="card card-body">
				<h2 class="card-title"><Star size={18} /> {t('profile.eval.latest')}</h2>
				<p class="text-sm text-muted">{latest.checklist.name} · {formatDate(latest.submitted_at)}</p>
				{#if overall(latest) !== null}
					<div class="flex items-end gap-3">
						<span class="font-display text-5xl font-extrabold text-ink">{fmt(overall(latest) ?? 0)}</span>
						<span class="pb-1.5 text-sm text-muted">{t('profile.eval.average')}</span>
					</div>
				{/if}
				{#if numeric.length}
					<ul class="mt-2 grid gap-2">
						{#each numeric as a (a.field_id)}
							<li class="flex items-center justify-between text-sm"><span class="text-muted">{a.label}</span><span class="font-mono text-xs font-semibold text-ink">{fmt(num(a.value) ?? 0)}</span></li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
		{#if points.length}
			<div class="card card-body">
				<h2 class="card-title text-base flex items-center gap-2"><TrendingUp size={16} class="shrink-0" aria-hidden="true" /> {t('profile.eval.trend')}</h2>
				<TrendChart {points} label={t('profile.eval.trend')} />
			</div>
		{/if}
	</div>
	{/if}

	<div class="card card-body">
		<div class="flex items-center justify-between gap-2">
			<h2 class="card-title"><ClipboardCheck size={18} /> {t('profile.eval.title')}</h2>
			{#if usable.length === 1}
				<a class="btn btn-outline btn-sm" href={evaluateHref(usable[0].id, memberId)}>{t('profile.eval.add')}</a>
			{:else if usable.length > 1}
				<details class="dropdown dropdown-end">
					<summary class="btn btn-outline btn-sm">{t('profile.eval.add')}</summary>
					<ul class="dropdown-content menu z-10 mt-1 w-56 rounded-lg border border-line bg-white p-1 shadow">
						{#each usable as c (c.id)}<li><a href={evaluateHref(c.id, memberId)}>{c.name}</a></li>{/each}
					</ul>
				</details>
			{/if}
		</div>
		{#if error}
			<p class="mt-2 text-sm text-error">{t('common.error.generic')}</p>
		{:else if !evaluations.length}
			<p class="mt-2 text-sm text-muted">{usable.length ? t('profile.eval.empty') : t('profile.eval.empty_no_checklist')}</p>
		{:else}
			<ol class="relative mt-2 grid gap-5 border-l-2 border-line pl-6">
				{#each evaluations as e (e.id)}
					{@const avg = overall(e)}
					<li class="relative">
						<span class="absolute top-1.5 -left-[1.9rem] h-3 w-3 rounded-full border-2 border-white bg-club ring-1 ring-club"></span>
						<div class="flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-semibold text-ink">{formatDate(e.submitted_at)} <span class="font-normal text-muted">· {e.checklist.name} · {e.season.name}</span></p>
								<p class="text-sm text-muted">{e.evaluator ? t('profile.eval.by', { name: e.evaluator.name }) : t('profile.eval.by_unknown')}</p>
							</div>
							<div class="flex items-center gap-2">
								{#if avg !== null}<span class="badge badge-neutral gap-1"><Star size={12} /> {fmt(avg)}</span>{/if}
								{#if mayDelete(e)}
									<form method="POST" action="?/deleteEvaluation" use:enhance={({ cancel }) => { if (!confirm(t('profile.eval.delete_confirm'))) cancel(); }}>
										<input type="hidden" name="evaluation_id" value={e.id} />
										<button class="btn btn-ghost btn-xs btn-square" type="submit" aria-label={t('profile.eval.delete')}><Trash2 size={13} /></button>
									</form>
								{/if}
							</div>
						</div>
						<div class="mt-2 flex flex-wrap gap-1.5">
							{#each e.answers.filter((a) => a.field_type !== 'textarea' && a.field_type !== 'text') as a (a.field_id)}
								<span class="badge badge-sm badge-outline">{a.label} <span class="ml-1 font-mono">{answerShown(a, t('common.yes'), t('common.no'))}</span></span>
							{/each}
						</div>
						{#each e.answers.filter((a) => a.field_type === 'textarea' || a.field_type === 'text') as a (a.field_id)}
							<p class="mt-2 rounded-lg bg-surface p-3 text-sm text-slate"><span class="mb-0.5 block text-xs font-semibold text-muted">{a.label}</span>{String(a.value)}</p>
						{/each}
						{#if mayDelete(e)}
							<form class="mt-3 grid gap-2 rounded-lg border border-line p-3" method="POST" action="?/releaseEvaluation" use:enhance>
								<input type="hidden" name="evaluation_id" value={e.id} />
								<div class="flex flex-wrap items-center justify-between gap-2">
									<p class="flex items-center gap-1.5 text-xs font-semibold text-muted"><Eye size={13} aria-hidden="true" />{t('eval.share.title')}</p>
									<span class="badge badge-sm gap-1 {e.release ? 'badge-success' : 'badge-neutral'}">{#if e.release}<Eye size={11} aria-hidden="true" />{:else}<Lock size={11} aria-hidden="true" />{/if}{t('eval.share.currently')}: {t(e.release === 'full' ? 'eval.share.full' : e.release === 'notes' ? 'eval.share.notes' : 'eval.share.none')}</span>
								</div>
								<label class="grid gap-1 text-sm">{t('eval.public_note')}<textarea class="textarea textarea-bordered w-full" name="public_note" rows="2" maxlength="2000">{e.public_note ?? ''}</textarea></label>
								<div class="flex flex-wrap items-end gap-2">
									<label class="grid gap-1 text-sm">{t('eval.share.level')}
										<select class="select select-bordered select-sm h-9 min-h-0" name="release">
											<option value="" selected={!e.release}>{t('eval.share.none')}</option>
											<option value="notes" selected={e.release === 'notes'}>{t('eval.share.notes')}</option>
											<option value="full" selected={e.release === 'full'}>{t('eval.share.full')}</option>
										</select>
									</label>
									<button class="btn btn-outline btn-sm h-9 min-h-0" type="submit">{t('common.save')}</button>
								</div>
								<p class="text-xs text-muted">{t('eval.share.help')}</p>
							</form>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}
	</div>
</div>
