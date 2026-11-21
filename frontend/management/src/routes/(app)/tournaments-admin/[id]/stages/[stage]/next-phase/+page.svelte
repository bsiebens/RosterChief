<script lang="ts">
	import { untrack } from 'svelte';
	import { ArrowDown, ArrowLeft, ArrowUp, ListOrdered } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, Runner, TBASE } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const run = new Runner();
	let conflicts = $state<string[]>([]);
	let done = $state(false);

	// The working order of each group (tournament team ids); starts as the computed standings.
	let order = $state<Record<string, string[]>>({});
	$effect(() => {
		const p = data.phase;
		untrack(() => {
			order = Object.fromEntries((p?.groups ?? []).map((g, i) => [g.group?.id ?? `_${i}`, g.standings.map((s) => s.team.id)]));
		});
	});
	const original = (gi: number) => data.phase?.groups[gi].standings.map((s) => s.team.id) ?? [];
	const key = (gi: number) => data.phase?.groups[gi].group?.id ?? `_${gi}`;
	function move(gi: number, from: number, to: number) {
		const list = [...(order[key(gi)] ?? [])];
		if (to < 0 || to >= list.length) return;
		[list[from], list[to]] = [list[to], list[from]];
		order[key(gi)] = list;
	}
	const byId = (gi: number, id: string) => data.phase!.groups[gi].standings.find((s) => s.team.id === id)!;

	async function confirm() {
		const changed: Record<string, string[]> = {};
		data.phase?.groups.forEach((g, gi) => {
			if (g.group && order[key(gi)]?.join() !== original(gi).join()) changed[g.group.id] = order[key(gi)];
		});
		const body = Object.keys(changed).length ? { order: changed } : {};
		await run.post<{ updated_count: number; conflicts: string[] }>(`/tournaments/${tn.id}/stages/${data.stageId}/next-phase`, body, (d) => {
			conflicts = d?.conflicts ?? [];
			done = true;
		}, { reload: false });
	}

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.next.title') }));
</script>

<svelte:head><title>{pageTitle(t('tn.next.title'))}</title></svelte:head>

<div class="grid max-w-4xl gap-4">
	<a class="link inline-flex w-fit items-center gap-1 text-sm" href="{TBASE}/{tn.id}/setup"><ArrowLeft size={14} /> {t('tn.seed.back')}</a>
	{#if data.error}
		<Alert kind={data.error.code === 'not_ready' ? 'warning' : 'error'}>{data.error.code === 'not_ready' ? t('tn.next.not_ready') : data.error.message || t('common.error.generic')}</Alert>
	{:else if data.phase}
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		{#if done}
			<Alert kind={conflicts.length ? 'warning' : 'success'} title={t('tn.next.done')}>
				{#if conflicts.length}<ul class="list-disc pl-4">{#each conflicts as c, i (i)}<li>{c}</li>{/each}</ul>{:else}{t('tn.next.done_body')}{/if}
			</Alert>
		{/if}
		<p class="text-sm text-muted">{t('tn.next.help')}</p>
		{#each data.phase.groups as g, gi (key(gi))}
			<section class="card card-body gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} class="shrink-0" aria-hidden="true" /> {g.group?.name ?? data.phase.stage.name}</h2>
				<ol class="grid gap-2">
					{#each order[key(gi)] ?? [] as id, i (id)}
						{@const row = byId(gi, id)}
						<li class="flex flex-wrap items-start gap-3 rounded-lg border border-line p-3">
							<span class="w-6 font-mono text-sm text-muted">{i + 1}</span>
							<div class="min-w-0 flex-1">
								<strong class="text-ink">{row.team.name}</strong>
								<p class="font-mono text-xs text-muted">{row.points ?? 0} {t('tn.standings.pts')}{row.goal_diff !== undefined ? ` · ${row.goal_diff > 0 ? '+' : ''}${row.goal_diff}` : ''}</p>
								<ul class="mt-1 grid gap-0.5 text-xs text-muted">
									{#each g.standings.find((s) => s.rank === i + 1)?.feeds ?? [] as f (f.match_id + f.side)}
										<li>{t('tn.next.feeds', { match: `${f.home_display} – ${f.away_display}` })}{f.scheduled_start ? ` · ${fmtDateTime(f.scheduled_start)}` : ''}{f.field ? ` · ${f.field.name}` : ''}</li>
									{/each}
								</ul>
							</div>
							{#if g.group}
								<div class="flex gap-1">
									<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('tn.next.up')} disabled={i === 0} onclick={() => move(gi, i, i - 1)}><ArrowUp size={14} /></button>
									<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('tn.next.down')} disabled={i === (order[key(gi)]?.length ?? 0) - 1} onclick={() => move(gi, i, i + 1)}><ArrowDown size={14} /></button>
								</div>
							{/if}
						</li>
					{/each}
				</ol>
			</section>
		{/each}
		<div class="flex justify-end"><button class="btn btn-primary" type="button" disabled={run.busy || done} onclick={confirm}>{t('tn.next.confirm')}</button></div>
	{/if}
</div>
