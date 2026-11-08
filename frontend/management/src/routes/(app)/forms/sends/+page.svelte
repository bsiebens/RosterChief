<script lang="ts">
	import { page } from '$app/state';
	import { ClipboardList, Eye } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import SendStatusBadge from '#lib/components/forms/SendStatusBadge.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { sendHref } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const href = (patch: Partial<{ tab: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.tab !== 'open') sp.set('tab', f.tab);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const tabs = $derived([
		{ key: 'open', label: t('forms.sends.tab.open') },
		{ key: 'scheduled', label: t('forms.sends.tab.scheduled') },
		{ key: 'closed', label: t('forms.sends.tab.closed') },
		{ key: 'all', label: t('forms.sends.tab.all') }
	]);
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');

	usePageHeader(() => ({ title: t('forms.sends.title'), context: data.count === 1 ? t('forms.sends.count_one') : t('forms.sends.count', { n: data.count }) }));
</script>

<svelte:head><title>{pageTitle(t('forms.sends.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
		<nav class="flex flex-wrap items-center gap-1" aria-label={t('forms.sends.tabs')}>
			{#each tabs as tab (tab.key)}
				<a
					href={href({ tab: tab.key })}
					aria-current={data.filters.tab === tab.key ? 'page' : undefined}
					class="flex h-8 items-center rounded-md px-3 font-display text-xs font-bold tracking-[.08em] uppercase {data.filters.tab === tab.key ? 'bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{tab.label}</a>
			{/each}
		</nav>
	</div>
	<div class="grid gap-4 px-7 py-5">
		{#if deleted}<Alert kind="success">{t('forms.sends.deleted')}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<ClipboardList size={28} />
				<p>{t('forms.sends.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('forms.sends.col.form')}</th><th>{t('forms.sends.col.audience')}</th><th>{t('forms.sends.col.window')}</th><th>{t('forms.sends.col.status')}</th><th class="text-right">{t('forms.sends.col.responded')}</th><th><span class="sr-only">{t('forms.col.actions')}</span></th></tr>
					</thead>
					<tbody>
						{#each data.rows as s (s.id)}
							<tr class="hover:bg-surface">
								<td><a class="link font-semibold" href={sendHref(s.id)}>{s.form_title}</a></td>
								<td class="text-sm text-muted">{s.audience_label}</td>
								<td class="font-mono text-xs text-muted">
									{#if s.opens_at}<div>{t('forms.sends.from', { date: formatDateTime(s.opens_at) })}</div>{/if}
									{#if s.closes_at}<div>{t('forms.sends.until', { date: formatDateTime(s.closes_at) })}</div>{/if}
									{#if !s.opens_at && !s.closes_at}{t('common.none')}{/if}
								</td>
								<td><SendStatusBadge status={s.status} small /></td>
								<td class="text-right font-mono text-sm whitespace-nowrap">{t('forms.sends.x_of_y', { x: s.response_count, y: s.audience_count })}</td>
								<td class="text-right"><a class="btn btn-outline btn-xs gap-1" href={sendHref(s.id)} aria-label={t('forms.sends.open_named', { title: s.form_title })}><Eye size={13} /> {t('forms.sends.responses')}</a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
