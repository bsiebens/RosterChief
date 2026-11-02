<script lang="ts">
	import { goto, invalidate } from '$app/navigation';
	import { ChevronLeft, ChevronRight, Search } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import PeriodsTable from '#lib/components/billing/periods/PeriodsTable.svelte';

	let { data } = $props();

	const canIssue = $derived(data.auth.status === 'authenticated' && data.auth.me.level === 'superuser');

	let q = $state('');
	$effect(() => {
		q = data.query.q;
	});

	const STATUS_OPTIONS = [
		['owing', 'Owing'],
		['unpaid', 'Unpaid'],
		['partial', 'Partially paid'],
		['paid', 'Paid'],
		['waived', 'Waived'],
		['cancelled', 'Cancelled'],
		['all', 'Any status']
	];
	const STATE_OPTIONS = [
		['', 'Any state'],
		['overdue', 'Overdue'],
		['in_grace', 'In grace'],
		['issued_ahead', 'Upcoming'],
		['settled', 'Settled']
	];
	const CHIPS = [
		{ label: 'Owing', status: 'owing', state: '' },
		{ label: 'Overdue', status: 'owing', state: 'overdue' },
		{ label: 'In grace', status: 'owing', state: 'in_grace' },
		{ label: 'Upcoming', status: 'owing', state: 'issued_ahead' },
		{ label: 'Paid', status: 'paid', state: '' },
		{ label: 'All', status: 'all', state: '' }
	];

	function href(over: Record<string, string | number>) {
		const p = new URLSearchParams();
		const merged: Record<string, string | number> = { q: data.query.q, status: data.query.status, state: data.query.state, club: data.query.club, plan: data.query.plan, page: data.query.page, ...over };
		for (const [k, v] of Object.entries(merged)) if (v !== '' && !(k === 'status' && v === 'owing') && !(k === 'page' && Number(v) === 1)) p.set(k, String(v));
		const s = p.toString();
		return `/billing${s ? `?${s}` : ''}`;
	}
	const go = (over: Record<string, string | number>) => goto(href({ page: 1, ...over }), { reset: false });

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		timer = setTimeout(() => goto(href({ q: q.trim(), page: 1 }), { reset: false, replaceState: true }), 300);
	}

	const chipActive = (c: (typeof CHIPS)[number]) => data.query.status === c.status && data.query.state === c.state && !data.query.club && !data.query.plan && !data.query.q;
	const filtered = $derived(!!(data.query.q || data.query.state || data.query.club || data.query.plan || data.query.status !== 'owing'));

	const pages = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const from = $derived(data.total ? (data.query.page - 1) * data.pageSize + 1 : 0);
	const to = $derived(Math.min(data.total, data.query.page * data.pageSize));
</script>

<svelte:head><title>Billing periods · RosterChief</title></svelte:head>

<div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Quick filters">
	{#each CHIPS as chip (chip.label)}
		<a class="btn btn-xs {chipActive(chip) ? '' : 'btn-outline'}" href={`/billing${chip.status === 'owing' && !chip.state ? '' : `?${new URLSearchParams({ status: chip.status, ...(chip.state ? { state: chip.state } : {}) })}`}`} aria-current={chipActive(chip) ? 'true' : undefined}>{chip.label}</a>
	{/each}
</div>

{#if data.error}<Alert kind="error">{data.error}</Alert>{/if}

<div class="card">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
		<div class="relative w-full sm:w-64">
			<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" />
			<input class="input input-bordered w-full pl-9" type="search" placeholder="Club or invoice number" aria-label="Search periods by club or invoice number" bind:value={q} oninput={search} />
		</div>
		<select class="select select-bordered w-full sm:w-auto" aria-label="State" value={data.query.state} onchange={(e) => go({ state: e.currentTarget.value })}>
			{#each STATE_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
		</select>
		<select class="select select-bordered w-full sm:w-auto" aria-label="Payment status" value={data.query.status} onchange={(e) => go({ status: e.currentTarget.value })}>
			{#each STATUS_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
		</select>
		<select class="select select-bordered w-full sm:w-auto sm:max-w-44" aria-label="Plan" value={data.query.plan} onchange={(e) => go({ plan: e.currentTarget.value })}>
			<option value="">All plans</option>
			{#each data.plans as plan (plan.id)}<option value={plan.id}>{plan.name}</option>{/each}
		</select>
		<select class="select select-bordered w-full sm:w-auto sm:max-w-52" aria-label="Club" value={data.query.club} onchange={(e) => go({ club: e.currentTarget.value })}>
			<option value="">All clubs</option>
			{#each data.clubs as club (club.id)}<option value={club.id}>{club.name}</option>{/each}
		</select>
		<span class="ml-auto font-mono text-[11px] text-muted">{data.total} period{data.total === 1 ? '' : 's'}</span>
	</div>

	{#if data.dues.length}
		<PeriodsTable dues={data.dues} {canIssue} onchange={() => invalidate('app:billing')} />
	{:else}
		<p class="py-10 text-center text-sm text-muted">
			{#if filtered}No periods match these filters. <a class="link font-semibold" href="/billing?status=all">Show all periods</a>.{:else}Nothing is owed right now.{/if}
		</p>
	{/if}

	{#if data.total > data.pageSize}
		<div class="flex items-center justify-between border-t border-line px-4 py-2.5 font-mono text-[11px] text-muted">
			<span>{from}–{to} of {data.total}</span>
			<div class="flex items-center gap-2">
				<a class="btn btn-outline btn-xs {data.query.page <= 1 ? 'pointer-events-none opacity-40' : ''}" href={href({ page: data.query.page - 1 })} aria-label="Previous page"><ChevronLeft size={12} aria-hidden="true" /></a>
				<span>page {data.query.page} / {pages}</span>
				<a class="btn btn-outline btn-xs {data.query.page >= pages ? 'pointer-events-none opacity-40' : ''}" href={href({ page: data.query.page + 1 })} aria-label="Next page"><ChevronRight size={12} aria-hidden="true" /></a>
			</div>
		</div>
	{/if}
</div>
