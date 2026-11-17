<script lang="ts">
	import { onMount } from 'svelte';
	import { ArrowRightLeft, ChevronRight, ClipboardList, Download, IdCard, Ticket } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { formatDay, formatDateTime, parseIsoDate } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { PortalOverview, ProfilePerson } from '#lib/portal.ts';
	import { money } from '#lib/shop.ts';

	/** The family at a glance: memberships and their invoices, forms still to fill in, vouchers, and a way to another club. */
	let { overview, people, show = ['memberships', 'forms', 'vouchers', 'clubs'] }: { overview: PortalOverview; people: ProfilePerson[]; show?: ('memberships' | 'forms' | 'vouchers' | 'clubs')[] } = $props();

	type Club = { slug: string; name: string; host: string; logo_url?: string | null };
	let clubs = $state<Club[]>([]);
	onMount(async () => {
		const result = await api<Club[]>('GET', '/my-clubs');
		// Only offered when the person belongs to more than one club; the one they are in is left out of the list.
		if (result.ok && result.data && result.data.length > 1) clubs = result.data.filter((club) => club.host.split(':')[0] !== location.hostname);
	});
	const clubUrl = (club: Club) => `${location.protocol}//${club.host}${location.port ? ':' + location.port : ''}/`;
	const name = (id: string | null) => people.find((person) => person.id === id)?.name ?? '';
	const day = (value: string) => formatDay(parseIsoDate(value) ?? value);
	const feeTone = (status: string) => (status === 'paid' || status === 'waived' ? 'badge-success' : 'badge-warning');
	const statusTone = (status: string) => (status === 'active' ? 'badge-success' : status === 'pending' ? 'badge-warning' : 'badge-error');
</script>

<div class="grid min-w-0 gap-4">
	{#if show.includes('memberships')}<section class="card" aria-labelledby="mem-title">
		<div class="card-h"><IdCard size={17} aria-hidden="true" /><h2 id="mem-title">{t('portal.mem_title')}</h2><a class="link-more ml-auto" href="/member?tab=payments">{t('portal.payments')} →</a></div>
		<div class="divide-y divide-rule">
			{#each overview.memberships as row (row.member_id + row.season)}
				{@const person = people.find((entry) => entry.id === row.member_id)}
				<div class="flex flex-wrap items-center gap-3 px-5 py-3">
					<ClubCrest name={name(row.member_id)} logoUrl={person?.photo_url} size={32} />
					<span class="min-w-0 flex-1"><b class="block text-ink">{name(row.member_id)}</b><span class="text-xs text-muted">{row.season} · {t('portal.fee', { paid: money(row.paid), balance: money(row.balance) })}</span></span>
					<span class="flex flex-wrap items-center gap-1.5">
						<span class="badge badge-sm {statusTone(row.status)}">{t(`portal.mem_status_${row.status}`)}</span>
						<span class="badge badge-sm {feeTone(row.fee_status)}">{t(`portal.mem_fee_${row.fee_status}`)}</span>
						{#if row.invoice_id}<a class="btn btn-outline btn-sm gap-1.5" href="/api/v1/portal/invoices/{row.invoice_id}.pdf" download="{row.invoice_number ?? row.invoice_id}.pdf" target="_blank" rel="noreferrer"><Download size={14} aria-hidden="true" />{t('portal.mem_invoice', { number: row.invoice_number ?? '' })}</a>{/if}
					</span>
				</div>
			{:else}
				<p class="px-5 py-4 text-sm text-muted">{t('portal.req_no_membership')}</p>
			{/each}
		</div>
	</section>{/if}

	{#if show.includes('forms')}<section class="card" aria-labelledby="pf-title">
		<div class="card-h"><ClipboardList size={17} aria-hidden="true" /><h2 id="pf-title">{t('portal.forms_open')}</h2><a class="link-more ml-auto" href="/member/forms">{t('portal.forms')} →</a></div>
		<div class="divide-y divide-rule">
			{#each overview.forms as form (`${form.id}-${form.member_id}`)}
				<a class="flex min-h-14 items-center gap-3 px-5 py-2 hover:bg-subhead" href="/member/forms?fill={form.id}.{form.member_id}"><span class="min-w-0 flex-1"><b class="block text-ink">{form.title}</b><span class="text-xs text-muted">{name(form.member_id)}{form.closes_at ? ` · ${t('pubform.closes', { date: formatDateTime(form.closes_at) })}` : ''}</span></span><ChevronRight size={16} class="text-muted" aria-hidden="true" /></a>
			{:else}
				<p class="px-5 py-4 text-sm text-muted">{t('portal.no_forms')}</p>
			{/each}
		</div>
	</section>{/if}

	{#if show.includes('vouchers')}<section class="card" aria-labelledby="vo-title">
		<div class="card-h"><Ticket size={17} aria-hidden="true" /><h2 id="vo-title">{t('portal.vouchers')}</h2></div>
		<div class="divide-y divide-rule">
			{#each overview.vouchers as voucher (voucher.id)}
				<div class="flex items-center gap-3 px-5 py-3 {voucher.expired ? 'opacity-60' : ''}">
					<span class="min-w-0 flex-1"><b class="block text-ink">{voucher.number}</b><span class="text-xs text-muted">{voucher.member_id ? name(voucher.member_id) + ' · ' : ''}{t(voucher.expired ? 'portal.voucher_expired' : 'portal.voucher_until', { date: day(voucher.expiry_date) })}</span></span>
					<span class="text-right"><b class="block font-mono text-ink tabular-nums">{money(voucher.available)}</b><span class="text-xs text-muted">{t('portal.voucher_of', { amount: money(voucher.amount) })}</span></span>
				</div>
			{:else}
				<p class="px-5 py-4 text-sm text-muted">{t('portal.voucher_none')}</p>
			{/each}
		</div>
	</section>{/if}

	{#if show.includes('clubs') && clubs.length}
		<section class="card" aria-labelledby="sw-title">
			<div class="card-h"><ArrowRightLeft size={17} aria-hidden="true" /><h2 id="sw-title">{t('portal.switch_club')}</h2></div>
			<div class="divide-y divide-rule">
				{#each clubs as club (club.slug)}
					<a class="flex min-h-14 items-center gap-3 px-5 py-2 hover:bg-subhead" href={clubUrl(club)} data-sveltekit-reload><ClubCrest name={club.name} logoUrl={club.logo_url} size={32} /><span class="min-w-0 flex-1 font-semibold text-ink">{club.name}</span><ChevronRight size={16} class="text-muted" aria-hidden="true" /></a>
				{/each}
			</div>
		</section>
	{/if}
</div>
