<script lang="ts">
	import { Check, ChevronRight, FileText } from '@lucide/svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { ParentHome } from '#lib/portal.ts';
	import { day, money } from '#lib/shop.ts';

	/**
	 * Payments on a phone: the next payment first (what, how much, by when, and how to pay), then one card per child with how much of the fee is
	 * paid and a short timeline (payments received, the next one due), then the invoices and receipts. A late instalment says what it blocks.
	 */
	let { home, people, selected = $bindable(''), memberName }: { home: ParentHome; people: ParentHome['people']; selected: string; memberName: (id: string) => string } = $props();

	const fees = $derived(home.fees.filter((fee) => !selected || fee.member_id === selected));
	const charges = $derived((home.charges ?? []).filter((charge) => charge.status !== 'paid' && (!selected || charge.member_id === selected)));
	const received = (memberId: string, season: string) => (home.payments ?? []).filter((payment) => payment.member_id === memberId && payment.season === season).sort((a, b) => a.paid_at.localeCompare(b.paid_at));
	const next = $derived(
		fees
			.filter((fee) => fee.next_installment)
			.map((fee) => ({ fee, due: fee.next_installment! }))
			.sort((a, b) => a.due.due_date.localeCompare(b.due.due_date))[0] ?? null
	);
	const open = $derived(fees.reduce((sum, fee) => sum + Number(fee.balance), 0));
	const daysLeft = (iso: string) => Math.ceil((Date.parse(iso) - Date.now()) / 864e5);
</script>

<div class="grid min-w-0 gap-5">

	<section class="grid gap-3 rounded-box bg-sidebar-bg p-4 text-sidebar-fg" aria-label={t('portal.pay_next')}>
		{#if next}
			<div>
				<p class="font-mono text-[11px] tracking-[.12em] text-sidebar-fg-dim uppercase">{t('portal.pay_next')} · {memberName(next.fee.member_id)}</p>
				<p class="mt-1 font-display text-4xl leading-none font-extrabold uppercase">{money(next.due.remaining)} · {day(next.due.due_date)}</p>
			</div>
			<p class="text-sm {next.due.late ? 'font-semibold text-danger' : 'text-sidebar-fg-dim'}">{next.due.late ? t('portal.installment_late', { date: day(next.due.due_date), amount: money(next.due.remaining) }) : t('portal.pay_in_days', { n: daysLeft(next.due.due_date) })}</p>
		{:else if open > 0}
			<div>
				<p class="font-mono text-[11px] tracking-[.12em] text-sidebar-fg-dim uppercase">{t('portal.pay_open')}</p>
				<p class="mt-1 font-display text-4xl leading-none font-extrabold uppercase">{money(String(open))}</p>
			</div>
		{:else}
			<p class="flex items-center gap-2 font-display text-2xl font-extrabold uppercase"><Check size={22} aria-hidden="true" />{t('portal.pay_all_paid')}</p>
		{/if}
		{#if home.payment_instructions}
			<details class="rounded-box bg-white/10 px-3 py-2">
				<summary class="flex min-h-10 cursor-pointer list-none items-center justify-between font-semibold [&::-webkit-details-marker]:hidden">{t('portal.pay_how')}<ChevronRight size={16} aria-hidden="true" /></summary>
				<p class="pt-2 text-sm whitespace-pre-line">{home.payment_instructions}</p>
			</details>
		{/if}
	</section>

	{#each fees as fee (`${fee.member_id}-${fee.season}`)}
		{@const paid = Number(fee.paid)}
		{@const total = paid + Number(fee.balance)}
		<section class="grid gap-2">
			<h2 class="flex items-baseline gap-2 font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{people.length > 1 ? `${memberName(fee.member_id)} · ` : ''}{fee.season}<span class="ml-auto font-mono text-[11px] font-normal tracking-normal text-muted normal-case">{t('portal.pay_of', { paid: money(fee.paid), total: money(String(total)) })}</span></h2>
			<div class="card grid gap-3 p-4">
				<div class="h-2 overflow-hidden rounded-full bg-subhead" role="progressbar" aria-valuenow={total > 0 ? Math.round((paid / total) * 100) : 100} aria-valuemin="0" aria-valuemax="100"><div class="h-full rounded-full bg-ok" style="width: {total > 0 ? Math.min(100, (paid / total) * 100) : 100}%"></div></div>
				<ul class="grid gap-2.5">
					{#each received(fee.member_id, fee.season) as payment (payment.id)}
						<li class="flex items-center gap-3"><span class="grid size-7 shrink-0 place-items-center rounded-full bg-ok text-white"><Check size={15} aria-hidden="true" /></span><span class="min-w-0 flex-1"><b class="text-ink">{money(payment.amount)}</b> <span class="text-sm text-muted">· {formatDateTime(payment.paid_at)}</span></span><span class="badge badge-sm badge-success">{t('portal.charge_paid')}</span></li>
					{/each}
					{#if fee.next_installment}
						<li class="flex items-center gap-3"><span class="size-7 shrink-0 rounded-full border-2 {fee.next_installment.late ? 'border-danger' : 'border-warn-text'}"></span><span class="min-w-0 flex-1"><b class="text-ink">{money(fee.next_installment.remaining)}</b> <span class="text-sm text-muted">· {day(fee.next_installment.due_date)}</span></span><span class="badge badge-sm {fee.next_installment.late ? 'badge-error' : 'badge-warn'}">{fee.next_installment.late ? t('portal.charge_overdue') : t('portal.pay_due_in', { n: daysLeft(fee.next_installment.due_date) })}</span></li>
					{:else if Number(fee.balance) > 0}
						<li class="flex items-center gap-3"><span class="size-7 shrink-0 rounded-full border-2 border-warn-text"></span><span class="min-w-0 flex-1"><b class="text-ink">{money(fee.balance)}</b> <span class="text-sm text-muted">· {t('portal.pay_open')}</span></span></li>
					{/if}
				</ul>
			</div>
			{#if fee.next_installment?.late}<p class="px-0.5 text-sm text-muted">{t('portal.pay_late_blocks')}</p>{/if}
		</section>
	{:else}
		<p class="card card-body text-sm text-muted">{t('portal.no_payments')}</p>
	{/each}

	{#if charges.length}
		<section class="grid gap-2">
			<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.charges')}</h2>
			<div class="card divide-y divide-rule overflow-hidden">
				{#each charges as charge (charge.id)}
					<a class="flex min-h-14 items-center gap-3 px-4 py-2" href="/api/v1/portal/charges/{charge.id}.pdf" download="{charge.number}.pdf" target="_blank" rel="noreferrer"><span class="min-w-0 flex-1"><b class="block truncate text-ink">{charge.summary}</b><span class="block text-sm {charge.status === 'overdue' ? 'font-semibold text-danger' : 'text-muted'}">{charge.number}{charge.due_date ? ` · ${t('portal.due_by', { when: day(charge.due_date) })}` : ''}</span></span><b class="tabular-nums">{money(charge.due)}</b></a>
				{/each}
			</div>
		</section>
	{/if}

	{#if home.invoices.length}
		<section class="grid gap-2">
			<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.pay_invoices')}</h2>
			<div class="card divide-y divide-rule overflow-hidden">
				{#each home.invoices as invoice (invoice.id)}
					<a class="flex min-h-14 items-center gap-3 px-4" href="/api/v1/portal/invoices/{invoice.id}.pdf" download="{invoice.number}.pdf" target="_blank" rel="noreferrer"><span class="grid size-10 shrink-0 place-items-center rounded-box bg-subhead text-ink"><FileText size={18} aria-hidden="true" /></span><span class="min-w-0 flex-1"><b class="block text-ink">{t('portal.invoice', { number: invoice.number })}</b>{#if invoice.due}<span class="block text-sm text-muted">{t('portal.due_by', { when: day(invoice.due) })}</span>{/if}</span><span class="btn btn-outline btn-sm">PDF</span></a>
				{/each}
			</div>
		</section>
	{/if}
</div>
