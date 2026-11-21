<script lang="ts">
	import { ArrowLeft, Printer } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, fmtTime, TBASE } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tasks.print'), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => window.print()}><Printer size={16} /> {t('tn.tasks.print_now')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('tn.tasks.print'))}</title></svelte:head>

<div class="grid gap-4">
	<a class="link inline-flex w-fit items-center gap-1 text-sm print:hidden" href="{TBASE}/{tn.id}/tasks"><ArrowLeft size={14} /> {t('tn.tasks.back')}</a>
	{#if data.error || !data.print}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		<p class="font-mono text-xs text-muted">{fmtDateTime(data.print.timeline_start)} – {fmtTime(data.print.timeline_end)}</p>
		{#each data.print.locations as loc (loc.location_label)}
			<section class="card card-body gap-3 break-inside-avoid">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{loc.location_label || t('tn.tasks.no_location')}</h2>
				<div class="relative h-8 rounded bg-paper" aria-hidden="true">
					{#each loc.tasks as task (task.id)}
						<div class="absolute top-1 h-6 overflow-hidden rounded border border-line bg-white px-1 text-[10px] leading-6" style:left="{task.timeline_left_pct}%" style:width="{task.timeline_width_pct}%">{task.title}</div>
					{/each}
				</div>
				<table class="table table-sm">
					<thead><tr><th>{t('tn.tasks.f.title')}</th><th>{t('tn.tasks.when')}</th><th>{t('tn.tasks.volunteers')}</th></tr></thead>
					<tbody>
						{#each loc.tasks as task (task.id)}
							<tr>
								<td class="font-semibold">{task.title}</td>
								<td class="font-mono text-xs whitespace-nowrap">{fmtDateTime(task.starts_at)} – {fmtTime(task.ends_at)}</td>
								<td class="text-sm">{task.claims.map((c) => c.label || c.member.name).join(', ') || '—'}{#if task.open_slots} <span class="text-muted">({t('tn.tasks.open', { n: task.open_slots })})</span>{/if}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</section>
		{/each}
	{/if}
</div>
