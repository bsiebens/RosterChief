<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ArrowLeft, ClipboardCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import { answerShown, evaluateHref, fmt, meetingHref, type StatRow } from '#lib/evaluations.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { optionLabel } from '#lib/forms.ts';

	let { data } = $props();
	const view = $derived(page.url.searchParams.get('view') === 'matrix' ? 'matrix' : 'stats');
	const href = (patch: { view?: string; season?: string }) => {
		const sp = new URLSearchParams();
		const v = patch.view ?? view;
		const s = patch.season ?? data.season;
		if (v !== 'stats') sp.set('view', v);
		sp.set('season', s);
		return `?${sp}`;
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const tabs = $derived([{ key: 'stats', label: t('eval.results.stats') }, { key: 'matrix', label: t('eval.results.matrix') }]);
	const peak = (r: StatRow) => Math.max(1, ...r.distribution.map(([, n]) => n));

	usePageHeader(() => ({ title: t('eval.results.title'), context: data.checklist.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/evaluations"><ArrowLeft size={16} /> {t('eval.back')}</a>
	{#if data.checklist.is_active && data.checklist.criteria_count}<a class="btn btn-primary gap-2" href={evaluateHref(data.checklist.id)}><ClipboardCheck size={16} /> {t('eval.evaluate')}</a>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('eval.results.title'))}</title></svelte:head>

<div class="grid gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<FilterTabs {tabs} active={view} label={t('eval.results.view')} hrefFor={(key) => href({ view: key })} onnavigate={navigate} />
		<label class="flex items-center gap-2 text-sm">
			<span class="text-muted">{t('eval.results.season')}</span>
			<select class="select select-bordered select-sm" value={data.season} onchange={(e) => navigate(href({ season: e.currentTarget.value }))}>
				<option value="all">{t('eval.results.all_seasons')}</option>
				{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
			</select>
		</label>
	</div>

	{#if data.error || !data.stats || !data.matrix}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.stats.evaluation_count}
		<div class="card card-body items-center py-10 text-center text-muted"><p>{t('eval.results.empty')}</p></div>
	{:else if view === 'stats'}
		<p class="font-mono text-xs text-muted">{t('eval.results.count', { n: data.stats.evaluation_count })}</p>
		<div class="grid gap-4 lg:grid-cols-2">
			{#each data.stats.rows as r (r.field.id)}
				<section class="card card-body gap-3">
					<header>
						<h2 class="font-semibold text-ink">{r.field.label}</h2>
						<p class="font-mono text-xs text-muted">{t('eval.results.responses', { n: r.response_count })}</p>
					</header>
					{#if r.kind === 'numeric'}
						{#if r.average !== null}
							<div class="flex items-end gap-4">
								<div><p class="font-display text-4xl font-extrabold text-ink">{fmt(r.average)}</p><p class="text-xs text-muted">{t('eval.results.average')}</p></div>
								<dl class="grid grid-cols-2 gap-x-4 text-sm">
									<dt class="text-muted">{t('eval.results.min')}</dt><dd class="font-mono">{r.minimum !== null ? fmt(r.minimum) : ''}</dd>
									<dt class="text-muted">{t('eval.results.max')}</dt><dd class="font-mono">{r.maximum !== null ? fmt(r.maximum) : ''}</dd>
								</dl>
							</div>
						{/if}
					{:else if r.kind === 'distribution'}
						<ul class="grid gap-2">
							{#each r.distribution as [value, n] (value)}
								<li>
									<div class="flex justify-between text-sm"><span>{r.field.field_type === 'checkbox' ? (value === 'true' ? t('common.yes') : t('common.no')) : optionLabel(r.field, value)}</span><span class="font-mono text-xs">{n}</span></div>
									<div class="mt-1 h-2 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full bg-club" style="width: {(n / peak(r)) * 100}%"></div></div>
								</li>
							{/each}
						</ul>
					{:else if r.recent_answers.length}
						<ul class="grid gap-2">
							{#each r.recent_answers as a, i (i)}<li class="rounded-lg bg-surface p-2.5 text-sm text-slate">{a}</li>{/each}
						</ul>
					{/if}
					{#if !r.response_count}<p class="text-sm text-muted">{t('eval.results.no_answers')}</p>{/if}
				</section>
			{/each}
		</div>
	{:else}
		<p class="text-sm text-muted">{t('eval.results.matrix_help')}</p>
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th>{t('eval.col.player')}</th>
						{#each data.matrix.fields as f (f.id)}<th class="whitespace-nowrap">{f.label}</th>{/each}
						<th class="whitespace-nowrap">{t('eval.col.evaluated')}</th>
					</tr>
				</thead>
				<tbody>
					{#each data.matrix.rows as r (r.player.id)}
						<tr class="hover:bg-surface">
							<td class="font-semibold"><a class="link" href="{meetingHref(data.checklist.id, r.player.id)}">{r.player.name}</a></td>
							{#each data.matrix.fields as f (f.id)}
								{@const v = r.values[f.id]}
								<td class={v === null || v === undefined ? 'text-dim' : f.field_type === 'number' ? 'font-mono' : ''}>{v === null || v === undefined || v === '' ? '–' : answerShown({ field_type: f.field_type, options: f.options, value: v }, t('common.yes'), t('common.no'))}</td>
							{/each}
							<td class="font-mono text-xs whitespace-nowrap text-muted">{new Date(r.evaluated_at).toLocaleDateString()}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
