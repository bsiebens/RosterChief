<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { BellRing, ChevronRight, FilePlus2, FileText } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { day, money, statusBadge, STATUS_TABS, type InvoiceType, type TypeSummary } from '#lib/invoices.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data: loaded, form } = $props();
	// Defaults keep the page rendering while hot reload or a failed load leaves part of the data out.
	const data = $derived({ ...loaded, types: loaded.types ?? [], rows: loaded.rows ?? [], summary: loaded.summary ?? {}, filters: loaded.filters ?? { q: '', tab: 'all', type: 'all', offset: 0 } });
	const href = (patch: Partial<{ q: string; tab: string; type: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.type !== 'all') sp.set('type', f.type);
		if (f.tab !== 'all') sp.set('tab', f.tab);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.tab !== 'all' || data.filters.type !== 'all');

	// Counts and amounts of the kind(s) in view: one kind when filtered to it, otherwise all the kinds this person may see.
	const shown = $derived<InvoiceType[]>(data.filters.type === 'all' ? data.types : data.types.filter((x: InvoiceType) => x === data.filters.type));
	const sum = (pick: (s: TypeSummary) => number) => shown.reduce((n: number, k: InvoiceType) => n + (data.summary[k] ? pick(data.summary[k]!) : 0), 0);
	const amount = (pick: (s: TypeSummary) => string) => shown.reduce((n: number, k: InvoiceType) => n + (data.summary[k] ? Number(pick(data.summary[k]!)) : 0), 0);
	const counts = $derived<Record<string, number>>({
		all: sum((s) => s.drafts + s.open + s.paid + s.cancelled),
		draft: sum((s) => s.drafts),
		open: sum((s) => s.open),
		overdue: sum((s) => s.overdue),
		paid: sum((s) => s.paid),
		cancelled: sum((s) => s.cancelled)
	});
	const filterTabs = $derived(STATUS_TABS.map((key) => ({ key, label: t(`inv.tab.${key}`), count: counts[key], warn: key === 'overdue' && counts.overdue > 0 })));
	const canCharge = $derived(data.types.includes('charge'));

	let settingsOpen = $state(false);
	let busy = $state(false);
	let s = $state({ reminders_enabled: true, after_days: 3, repeat_days: 7, max_reminders: 3, invoice_due_days: 14 });
	function openSettings() {
		if (data.settings) s = { ...data.settings };
		settingsOpen = true;
	}
	const saved = $derived(form && 'done' in form && form.done === 'saveSettings');
	const failure = $derived(form && 'op' in form && !('done' in form) ? String(form.message || t('common.error.generic')) : '');
	const errs = $derived<Record<string, string>>(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});

	usePageHeader(() => ({ title: t('inv.title'), context: data.count === 1 ? t('inv.count_one') : t('inv.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	{#if data.settings}<button class="btn btn-outline gap-2" type="button" onclick={openSettings}><BellRing size={16} /> {t('chg.settings.open')}</button>{/if}
	{#if canCharge}<a class="btn btn-primary gap-2" href="/finance/charges/new"><FilePlus2 size={16} /> {t('inv.new_charge')}</a>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('inv.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('inv.search')} searchPlaceholder={t('inv.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.tab} label={t('inv.filter_label')} hrefFor={(key) => href({ tab: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<div class="card p-4"><div class="text-[13px] text-muted">{t('inv.kpi.open')}</div><div class="font-mono text-2xl font-semibold text-ink">{money(amount((x) => x.open_amount))}</div><div class="text-xs text-muted">{counts.open === 1 ? t('inv.count_one') : t('inv.count', { n: counts.open })}</div></div>
			<div class="card p-4"><div class="text-[13px] text-muted">{t('inv.kpi.overdue')}</div><div class="font-mono text-2xl font-semibold {counts.overdue ? 'text-danger' : 'text-ink'}">{money(amount((x) => x.overdue_amount))}</div><div class="text-xs text-muted">{counts.overdue === 1 ? t('inv.count_one') : t('inv.count', { n: counts.overdue })}</div></div>
			{#if canCharge}<div class="card p-4"><div class="text-[13px] text-muted">{t('inv.kpi.drafts')}</div><div class="font-mono text-2xl font-semibold text-ink">{counts.draft}</div></div>{/if}
		</div>
		{#if data.types.length > 1}
			<div class="flex flex-wrap items-center gap-2" role="group" aria-label={t('inv.type_label')}>
				{#each ['all', ...data.types] as key (key)}
					<a class="btn btn-sm {data.filters.type === key ? 'btn-neutral' : 'btn-outline'}" href={href({ type: key })} data-sveltekit-reset="false" aria-current={data.filters.type === key ? 'true' : undefined}>{t(`inv.type.${key}`)}</a>
				{/each}
			</div>
		{/if}
		{#if saved}<Alert kind="success">{t('chg.settings.saved')}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('inv.error')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<FileText size={28} />
				<p>{filtered ? t('inv.empty_filtered') : t('inv.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('inv.col.number')}</th>
							{#if data.types.length > 1}<th class="hidden sm:table-cell">{t('inv.col.kind')}</th>{/if}
							<th>{t('inv.col.member')}</th>
							<th class="hidden md:table-cell">{t('inv.col.for')}</th>
							<th>{t('inv.col.status')}</th>
							<th class="hidden text-right lg:table-cell">{t('inv.col.total')}</th>
							<th class="text-right">{t('inv.col.due')}</th>
							<th class="hidden xl:table-cell">{t('inv.col.due_date')}</th>
							<th><span class="sr-only">{t('common.edit')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as r (`${r.type}-${r.id}`)}
							<tr class="cursor-pointer hover:bg-paper" onclick={(e) => { if (!(e.target as HTMLElement).closest('a, button')) goto(r.href); }}>
								<td><a class="link font-mono text-xs font-semibold" href={r.href}>{r.number ?? t('inv.draft')}</a></td>
								{#if data.types.length > 1}<td class="hidden sm:table-cell"><span class="badge badge-sm badge-outline">{t(`inv.type.${r.type}_one`)}</span></td>{/if}
								<td class="font-semibold whitespace-nowrap">{r.member?.name ?? '—'}</td>
								<td class="hidden max-w-64 truncate text-sm md:table-cell" title={r.summary}>{r.summary}</td>
								<td><span class="badge badge-sm {statusBadge(r.status)}">{t(`inv.status.${r.status}`)}</span></td>
								<td class="hidden text-right font-mono text-sm lg:table-cell">{money(r.total)}</td>
								<td class="text-right font-mono text-sm {Number(r.amount_due) > 0 && r.status !== 'draft' && r.status !== 'cancelled' ? 'font-semibold text-warn-text' : 'text-muted'}">{r.status === 'cancelled' || r.status === 'draft' ? '' : money(r.amount_due)}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap text-muted xl:table-cell">{day(r.due_date)}</td>
								<td class="text-right"><div class="flex items-center justify-end gap-1"><a class="btn btn-ghost btn-xs btn-square" href={r.href} aria-label={r.number ?? t('inv.draft')}><ChevronRight size={16} /></a></div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>

<Modal bind:open={settingsOpen} title={t('chg.settings.title')} wide>
	<form
		method="POST"
		action="?/saveSettings"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'success') settingsOpen = false;
			};
		}}>
		<input type="hidden" name="payload" value={JSON.stringify({ ...s, after_days: Number(s.after_days), repeat_days: Number(s.repeat_days), max_reminders: Number(s.max_reminders), invoice_due_days: Number(s.invoice_due_days) })} />
		{#if failure}<Alert kind="error">{failure}</Alert>{/if}
		<p class="text-sm text-muted">{t('chg.settings.help')}</p>
		<label class="flex items-center gap-3"><input type="checkbox" class="toggle" bind:checked={s.reminders_enabled} /> <span>{t('chg.settings.enabled')}</span></label>
		<div class="grid gap-3 sm:grid-cols-3">
			<Field label={t('chg.settings.after')} type="number" min="0" max="90" bind:value={s.after_days as unknown as string} error={errs.after_days} disabled={!s.reminders_enabled} />
			<Field label={t('chg.settings.repeat')} type="number" min="1" max="90" bind:value={s.repeat_days as unknown as string} error={errs.repeat_days} disabled={!s.reminders_enabled} />
			<Field label={t('chg.settings.max')} type="number" min="0" max="20" bind:value={s.max_reminders as unknown as string} error={errs.max_reminders} disabled={!s.reminders_enabled} />
		</div>
		<Field label={t('chg.settings.terms')} type="number" min="0" max="365" bind:value={s.invoice_due_days as unknown as string} error={errs.invoice_due_days} help={t('chg.settings.terms_help')} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (settingsOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
