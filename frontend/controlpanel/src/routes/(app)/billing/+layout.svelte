<script lang="ts">
	import { page } from '$app/state';
	import { money } from '#lib/dashboard.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Kpi from '#lib/components/Kpi.svelte';

	let { data, children } = $props();

	const s = $derived(data.summary);
	const TABS = [
		{ href: '/billing', label: 'Periods' },
		{ href: '/billing/cycles', label: 'Cycles' },
		{ href: '/billing/plans', label: 'Plans & pricing' },
		{ href: '/billing/credit-notes', label: 'Credit notes' },
		{ href: '/billing/settings', label: 'Invoicing' },
		{ href: '/billing/help', label: 'How it works' }
	];
	const active = (href: string) => (href === '/billing' ? page.url.pathname === '/billing' : page.url.pathname.startsWith(href));
</script>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Billing</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">RosterChief invoicing its clubs, in cycles. Amounts in EUR; plan prices exclude VAT.</p>
</div>

{#if s}
	<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
		<Kpi label="owed" value={money(s.owed)} sub="incl. VAT, across all clubs" tone={Number(s.owed) ? 'warn' : 'none'} />
		<Kpi label="overdue" value={s.overdue_count} sub="past their grace period" tone={s.overdue_count ? 'club' : 'none'} />
		<Kpi label="in grace" value={s.in_grace_count} sub="started, not yet late" tone={s.in_grace_count ? 'warn' : 'none'} />
		<Kpi label="to send" value={s.invoices_to_send} sub="invoices not sent yet" tone={s.invoices_to_send ? 'warn' : 'none'} />
		<Kpi label="unbilled clubs" value={s.clubs_unbilled} sub="using the platform for free" />
	</div>
{/if}

<nav class="flex gap-1 border-b border-line" aria-label="Billing sections">
	{#each TABS as tab (tab.href)}
		<a
			class="-mb-px border-b-2 px-4 py-2 font-mono text-xs {active(tab.href) ? 'border-ice bg-white text-ink' : 'border-transparent text-muted hover:text-ink'}"
			href={tab.href}
			aria-current={active(tab.href) ? 'page' : undefined}>{tab.label}</a
		>
	{/each}
</nav>

{@render children()}
