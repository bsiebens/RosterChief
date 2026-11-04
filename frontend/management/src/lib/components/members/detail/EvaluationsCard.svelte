<script lang="ts">
	import { ClipboardCheck, Star } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { mockEvaluations } from '#lib/members/mock.ts';

	/** Sample content: the list of coach evaluations with the five skill scores. */
	const evaluations = mockEvaluations();
	const latest = evaluations[0];
	const history = [...evaluations].reverse();
	const bar = (v: number) => (v / 5) * 100;
	// Line chart: scores 0-5 up the side, evaluations oldest to newest across.
	const y = (v: number) => 120 - (v / 5) * 100;
	const points = history.map((e, i) => ({ id: e.id, overall: e.overall, label: formatDate(e.date).replace(/,? \d{4}$/, ''), x: history.length === 1 ? 160 : 50 + (i * 220) / (history.length - 1), y: y(e.overall) }));
</script>

<div class="grid gap-4 lg:grid-cols-[22rem_1fr]">
	<div class="grid content-start gap-4">
		<div class="card card-body sample-stripes">
			<h2 class="card-title"><Star size={18} /> {t('profile.eval.latest')}</h2>
			<div class="flex items-end gap-3">
				<span class="font-display text-5xl font-extrabold text-ink">{latest.overall.toFixed(1)}</span>
				<span class="pb-1.5 text-sm text-muted">/ 5 · {t('profile.eval.overall')}</span>
			</div>
			<ul class="mt-2 grid gap-2.5">
				{#each latest.scores as s (s.key)}
					<li>
						<div class="flex items-center justify-between text-sm"><span class="text-muted">{t(`profile.eval.skill.${s.key}`)}</span><span class="font-mono text-xs font-semibold text-ink">{s.value.toFixed(1)}</span></div>
						<div class="mt-1 h-2 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full bg-club" style="width: {bar(s.value)}%"></div></div>
					</li>
				{/each}
			</ul>
		</div>

		<div class="card card-body sample-stripes">
			<h2 class="card-title text-base">{t('profile.eval.trend')}</h2>
			<svg viewBox="0 0 300 150" class="w-full" role="img" aria-label={t('profile.eval.trend')}>
				{#each [1, 2, 3, 4, 5] as g (g)}
					<line x1="28" x2="292" y1={y(g)} y2={y(g)} class="stroke-line" stroke-width="1" />
					<text x="20" y={y(g) + 3.5} text-anchor="end" class="fill-muted font-mono" font-size="10">{g}</text>
				{/each}
				<polyline points={points.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" class="stroke-club" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
				{#each points as p (p.id)}
					<circle cx={p.x} cy={p.y} r="4.5" class="fill-club stroke-white" stroke-width="2" />
					<text x={p.x} y={p.y - 10} text-anchor="middle" class="fill-ink font-mono" font-size="11" font-weight="700">{p.overall.toFixed(1)}</text>
					<text x={p.x} y="146" text-anchor="middle" class="fill-muted" font-size="10">{p.label}</text>
				{/each}
			</svg>
		</div>
	</div>

	<div class="card card-body sample-stripes">
		<div class="flex items-center justify-between gap-2">
			<h2 class="card-title"><ClipboardCheck size={18} /> {t('profile.eval.title')}</h2>
			<button class="btn btn-outline btn-sm" type="button" disabled>{t('profile.eval.add')}</button>
		</div>
		<ol class="relative mt-2 grid gap-5 border-l-2 border-line pl-6">
			{#each evaluations as e (e.id)}
				<li class="relative">
					<span class="absolute top-1.5 -left-[1.9rem] h-3 w-3 rounded-full border-2 border-white bg-club ring-1 ring-club"></span>
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div>
							<p class="font-semibold text-ink">{formatDate(e.date)} <span class="font-normal text-muted">· {e.team} · {e.season}</span></p>
							<p class="text-sm text-muted">{t('profile.eval.by', { name: e.evaluator })}</p>
						</div>
						<span class="badge badge-neutral gap-1"><Star size={12} /> {e.overall.toFixed(1)}</span>
					</div>
					<div class="mt-2 flex flex-wrap gap-1.5">
						{#each e.scores as s (s.key)}<span class="badge badge-sm badge-outline">{t(`profile.eval.skill.${s.key}`)} <span class="ml-1 font-mono">{s.value}</span></span>{/each}
					</div>
					<p class="mt-2 rounded-lg bg-surface p-3 text-sm text-slate">{e.comment}</p>
				</li>
			{/each}
		</ol>
	</div>
</div>
