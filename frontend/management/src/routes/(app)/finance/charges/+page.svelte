<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { BellRing, ChevronRight, FilePlus2, FileText } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Field from '#lib/components/Field.svelte';
	import { chargeHref, day, money, statusBadge, TABS } from '#lib/charges.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; tab: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.tab !== 'all') sp.set('tab', f.tab);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const filtered = $derived(!!data.filters.q || data.filters.tab !== 'all');
	const counts = $derived<Record<string, number>>({
		all: data.summary.drafts + data.summary.open + data.summary.paid + data.summary.cancelled,
		draft: data.summary.drafts,
		open: data.summary.open,
		overdue: data.summary.overdue,
		paid: data.summary.paid,
		cancelled: data.summary.cancelled
	});
	const filterTabs = $derived(TABS.map((key) => ({ key, label: t(`chg.tab.${key}`), count: counts[key], warn: key === 'overdue' && counts.overdue > 0 })));

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

	usePageHeader(() => ({ title: t('chg.title'), context: data.count === 1 ? t('chg.count_one') : t('chg.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-outline gap-2" type="button" onclick={openSettings}><BellRing size={16} /> {t('chg.settings.open')}</button>
	<a class="btn btn-primary gap-2" href="/finance/charges/new"><FilePlus2 size={16} /> {t('chg.add')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('chg.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('chg.search')} searchPlaceholder={t('chg.search_placeholder')} searchClass="w-72" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })}>
		{#snippet tabs()}
			<FilterTabs tabs={filterTabs} active={data.filters.tab} label={t('chg.filter_label')} hrefFor={(key) => href({ tab: key })} onnavigate={(h) => goto(h, { reset: false })} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<div class="card p-4"><div class="text-[13px] text-muted">{t('chg.kpi.open')}</div><div class="font-mono text-2xl font-semibold text-ink">{money(data.summary.open_amount)}</div><div class="text-xs text-muted">{data.summary.open === 1 ? t('chg.count_one') : t('chg.count', { n: data.summary.open })}</div></div>
			<div class="card p-4"><div class="text-[13px] text-muted">{t('chg.kpi.overdue')}</div><div class="font-mono text-2xl font-semibold {data.summary.overdue ? 'text-danger' : 'text-ink'}">{money(data.summary.overdue_amount)}</div><div class="text-xs text-muted">{data.summary.overdue === 1 ? t('chg.count_one') : t('chg.count', { n: data.summary.overdue })}</div></div>
			<div class="card p-4"><div class="text-[13px] text-muted">{t('chg.kpi.drafts')}</div><div class="font-mono text-2xl font-semibold text-ink">{data.summary.drafts}</div></div>
		</div>
		{#if saved}<Alert kind="success">{t('chg.settings.saved')}</Alert>{/if}

		{#if data.error}
			<Alert kind="error">{t('chg.error')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<FileText size={28} />
				<p>{filtered ? t('chg.empty_filtered') : t('chg.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('chg.col.number')}</th>
							<th>{t('chg.col.member')}</th>
							<th class="hidden md:table-cell">{t('chg.col.for')}</th>
							<th>{t('chg.col.status')}</th>
							<th class="hidden text-right lg:table-cell">{t('chg.col.total')}</th>
							<th class="text-right">{t('chg.col.due')}</th>
							<th class="hidden xl:table-cell">{t('chg.col.due_date')}</th>
							<th><span class="sr-only">{t('common.edit')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as c (c.id)}
							<tr class="cursor-pointer hover:bg-paper" onclick={(e) => { if (!(e.target as HTMLElement).closest('a, button')) goto(chargeHref(c.id)); }}>
								<td><a class="link font-mono text-xs font-semibold" href={chargeHref(c.id)}>{c.number ?? t('chg.draft_no_number')}</a></td>
								<td class="font-semibold whitespace-nowrap">{c.member.name}</td>
								<td class="hidden max-w-64 truncate text-sm md:table-cell" title={c.summary}>{c.summary}</td>
								<td><span class="badge badge-sm {statusBadge(c.status)}">{t(`chg.status.${c.status}`)}</span></td>
								<td class="hidden text-right font-mono text-sm lg:table-cell">{money(c.total)}</td>
								<td class="text-right font-mono text-sm {Number(c.amount_due) > 0 && c.status !== 'draft' && c.status !== 'cancelled' ? 'font-semibold text-warn-text' : 'text-muted'}">{c.status === 'cancelled' || c.status === 'draft' ? '' : money(c.amount_due)}</td>
								<td class="hidden font-mono text-xs whitespace-nowrap text-muted xl:table-cell">{day(c.due_date)}</td>
								<td class="text-right"><a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href={chargeHref(c.id)} aria-label={c.number ?? t('chg.draft_no_number')}><ChevronRight size={16} /></a></td>
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
