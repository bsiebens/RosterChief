<script lang="ts">
	import { goto } from '$app/navigation';
	import { Banknote, Inbox, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import PaymentDialog from '#lib/components/finance/PaymentDialog.svelte';
	import { financeHref, paymentBadge, type OpenMember } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const href = (patch: Partial<{ q: string; payment: string; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.payment !== 'open') sp.set('payment', f.payment);
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const filtered = $derived(!!data.filters.q);
	const tabList = $derived([
		{ key: 'open', label: t('fin.tab.open'), count: data.openCount, warn: true },
		{ key: 'overdue', label: t('fin.tab.overdue') },
		{ key: 'installments', label: t('installments.title') },
		{ key: 'paid', label: t('fin.tab.paid') },
		{ key: 'all', label: t('fin.tab.all') }
	]);

	// Members with a fee still open outside the registrations (import, added by hand): paid one by one in the dialog.
	let payFor = $state<OpenMember | null>(null);
	let payOpen = $state(false);
	const payItem = $derived(payFor ? { lines: [{ membership_id: payFor.membership_id, member: payFor.member, balance: payFor.balance }], vouchers_enabled: data.vouchersEnabled } : { lines: [], vouchers_enabled: false });
	const showMembers = $derived(data.openMembers.length > 0 && ['open', 'all'].includes(data.filters.payment));
	const paid = $derived(form && 'done' in form && form.done === 'payMember');
	const payError = $derived(form && 'op' in form && form.op === 'payMember' ? String(form.message || t('common.error.generic')) : '');
	const payFields = $derived((form && 'op' in form && form.op === 'payMember' && 'fields' in form ? form.fields : {}) as Record<string, string>);

	usePageHeader(() => ({ title: t('fin.title'), context: t('fin.context', { n: data.openCount + data.openMembersCount }) }));
</script>

<svelte:head><title>{pageTitle(t('fin.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar value={data.filters.q} searchLabel={t('fin.search')} searchPlaceholder={t('fin.search_placeholder')} searchClass="w-80" onsearch={(q) => goto(href({ q }), { replace: true, reset: false })} showClear={filtered} onclear={() => navigate(href({ q: '' }))}>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.payment} label={t('fin.tabs')} hrefFor={(key) => href({ payment: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>

	{#if data.totals}
		{@const totals = data.totals}
		<div class="grid gap-4 px-7 pt-5 sm:grid-cols-3">
			<div class="card card-body gap-1">
				<span class="font-mono text-xs tracking-wide text-muted uppercase">{t('fin.kpi.invoiced')}</span>
				<p class="font-display text-3xl font-extrabold text-ink">{money(totals.invoiced)}</p>
			</div>
			<div class="card card-body gap-1">
				<span class="font-mono text-xs tracking-wide text-muted uppercase">{t('fin.kpi.received')}</span>
				<p class="font-display text-3xl font-extrabold text-ok-text">{money(totals.received)}</p>
			</div>
			<div class="card card-body gap-1">
				<span class="font-mono text-xs tracking-wide text-muted uppercase">{t('fin.kpi.open')}</span>
				<p class="font-display text-3xl font-extrabold text-ink">{money(totals.open)}</p>
				<p class="text-xs text-muted">{t('fin.kpi.overdue', { amount: money(totals.overdue) })}</p>
			</div>
		</div>
	{/if}

	<div class="grid gap-4 px-7 py-5">
		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Inbox size={28} />
				<p>{filtered ? t('fin.empty_filtered') : data.filters.payment === 'open' ? t('fin.empty_open') : t('fin.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('fin.col.contact')}</th>
							<th>{t('fin.col.invoice')}</th>
							<th>{t('fin.col.due_date')}</th>
							<th class="text-right">{t('fin.col.amount')}</th>
							<th class="text-right">{t('fin.col.paid')}</th>
							<th class="text-right">{t('fin.col.balance')}</th>
							<th>{t('fin.col.status')}</th>
							<th><span class="sr-only">{t('fin.col.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as r (r.id)}
							{@const name = r.contact?.name ?? t('reg.no_contact')}
							<tr class="hover:bg-surface">
								<td>
									<a class="link font-semibold" href={financeHref(r.id)}>{name}</a>
									<p class="text-xs text-muted">{r.season_name} · {t('fin.people_n', { n: r.people_count })}{#if r.contact?.email} · {r.contact.email}{/if}</p>
								</td>
								<td class="font-mono text-xs">{r.invoice_number ?? '–'}</td>
								<td class="font-mono text-xs whitespace-nowrap {r.payment_status === 'overdue' ? 'font-semibold text-error' : 'text-muted'}">
									{#if r.next_installment}
										{formatDay(r.next_installment.due_date)}
										<p class="text-xs">{money(r.next_installment.remaining)} · {t('installments.next_due')}</p>
									{:else}
										{!r.installment_offsets.length && r.invoice_due_date ? formatDay(r.invoice_due_date) : '–'}
									{/if}
								</td>
								<td class="text-right font-mono text-sm">{money(r.amount_due)}</td>
								<td class="text-right font-mono text-sm">{money(r.amount_paid)}</td>
								<td class="text-right font-mono text-sm font-semibold">{money(r.balance)}</td>
								<td>{#if r.legacy_invoice_only}<span class="badge badge-sm badge-outline">{t('legacy_invoice.read_only')}</span>{:else if r.payment_status}<span class="badge badge-sm {paymentBadge(r.payment_status)}">{t(`fin.status.${r.payment_status}`)}</span>{/if}</td>
								<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1"><a class="btn btn-outline btn-xs gap-1" href={financeHref(r.id)} aria-label={t('fin.open_named', { name })}><Banknote size={13} /> {t('fin.pay_person')}</a></div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}

		{#if paid}<Alert kind="success">{t('fin.paid_ok')}</Alert>{/if}
		{#if showMembers}
			<section class="grid gap-2" aria-labelledby="fin-open-members">
				<div>
					<h2 id="fin-open-members" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('fin.members.title', { n: data.openMembersCount })}</h2>
					<p class="text-xs text-muted">{t('fin.members.help')}</p>
				</div>
				<div class="card overflow-x-auto">
					<table class="table">
						<thead>
							<tr><th>{t('fin.person')}</th><th>{t('reg.col.season')}</th><th class="text-right">{t('fin.col.amount')}</th><th class="text-right">{t('fin.col.paid')}</th><th class="text-right">{t('fin.col.balance')}</th><th><span class="sr-only">{t('fin.col.actions')}</span></th></tr>
						</thead>
						<tbody>
							{#each data.openMembers as m (m.membership_id)}
								<tr class="hover:bg-surface">
									<td><a class="link font-semibold" href="/members/{m.member.id}">{m.member.name}</a>{#if m.member.email}<p class="text-xs text-muted">{m.member.email}</p>{/if}</td>
									<td class="text-sm">{m.season_name}</td>
									<td class="text-right font-mono text-sm">{money(m.fee_amount)}</td>
									<td class="text-right font-mono text-sm">{money(m.amount_paid)}</td>
									<td class="text-right font-mono text-sm font-semibold">{money(m.balance)}</td>
									<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1"><button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => ((payFor = m), (payOpen = true))} aria-label={t('fin.pay_for', { name: m.member.name })}><Banknote size={13} /> {t('fin.pay_person')}</button></div></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/if}
	</div>
</div>

{#if payFor}
	<PaymentDialog bind:open={payOpen} item={payItem} error={payError} fields={payFields} action="?/payMember" />
{/if}
