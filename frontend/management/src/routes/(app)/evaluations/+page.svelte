<script lang="ts">
	import { page } from '$app/state';
	import { ClipboardCheck, Pencil, Plus, Presentation, Table2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { checklistHref, evaluateHref, meetingHref, resultsHref } from '#lib/evaluations.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const canManage = $derived(!!data.me?.can_manage_evaluations);
	const canExecute = $derived(!!data.me?.can_execute_evaluations);

	usePageHeader(() => ({ title: t('eval.title'), context: t('eval.count', { n: data.rows.length }), actions }));
</script>

{#snippet actions()}
	{#if canManage}<a class="btn btn-primary gap-2" href="/evaluations/new"><Plus size={16} /> {t('eval.add')}</a>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('eval.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if page.url.searchParams.get('deleted') === '1'}<Alert kind="success">{t('eval.deleted')}</Alert>{/if}
	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.rows.length}
		<div class="card card-body items-center py-10 text-center text-muted">
			<ClipboardCheck size={28} />
			<p>{canManage ? t('eval.empty_manage') : t('eval.empty')}</p>
		</div>
	{:else}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th>{t('eval.col.checklist')}</th>
						<th class="text-right">{t('eval.col.criteria')}</th>
						<th class="text-right">{t('eval.col.evaluations')}</th>
						<th><span class="sr-only">{t('eval.col.actions')}</span></th>
					</tr>
				</thead>
				<tbody>
					{#each data.rows as c (c.id)}
						<tr class="hover:bg-surface {c.is_active ? '' : 'opacity-70'}">
							<td>
								{#if canManage}<a class="link font-semibold" href={checklistHref(c.id)}>{c.name}</a>{:else}<span class="font-semibold text-ink">{c.name}</span>{/if}
								{#if !c.is_active}<span class="badge badge-sm badge-warning ml-2">{t('eval.inactive')}</span>{/if}
								{#if c.description}<p class="text-sm text-muted">{c.description}</p>{/if}
							</td>
							<td class="text-right font-mono text-sm">{c.criteria_count}</td>
							<td class="text-right font-mono text-sm">{c.evaluation_count}</td>
							<td class="text-right whitespace-nowrap">
								<div class="flex justify-end gap-1">
									{#if canExecute}
										{#if c.is_active && c.criteria_count}<a class="btn btn-primary btn-xs gap-1" href={evaluateHref(c.id)}><ClipboardCheck size={13} /> {t('eval.evaluate')}</a>{/if}
										<a class="btn btn-outline btn-xs gap-1" href={meetingHref(c.id)}><Presentation size={13} /> {t('eval.meeting')}</a>
										<a class="btn btn-outline btn-xs gap-1" href={resultsHref(c.id)}><Table2 size={13} /> {t('eval.results')}</a>
									{/if}
									{#if canManage}<a class="btn btn-outline btn-xs gap-1" href={checklistHref(c.id)} aria-label={t('eval.edit_named', { name: c.name })}><Pencil size={13} /> {t('common.edit')}</a>{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
