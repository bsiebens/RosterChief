<script lang="ts">
	import { goto } from '$app/navigation';
	import { ChevronLeft, ChevronRight, Search } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import CreditNotePdf from '#lib/components/billing/credit/CreditNotePdf.svelte';
	import { day, money2, vatPct } from '#lib/components/billing/periods/format.ts';

	let { data } = $props();

	let q = $state('');
	$effect(() => {
		q = data.query.q;
	});

	function href(over: Record<string, string | number>) {
		const p = new URLSearchParams();
		const merged: Record<string, string | number> = { q: data.query.q, club: data.query.club, page: data.query.page, ...over };
		for (const [k, v] of Object.entries(merged)) if (v !== '' && !(k === 'page' && Number(v) === 1)) p.set(k, String(v));
		const s = p.toString();
		return `/billing/credit-notes${s ? `?${s}` : ''}`;
	}
	const go = (over: Record<string, string | number>) => goto(href({ page: 1, ...over }), { reset: false });

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		timer = setTimeout(() => goto(href({ q: q.trim(), page: 1 }), { reset: false, replaceState: true }), 300);
	}

	const filtered = $derived(!!(data.query.q || data.query.club));
	const pages = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const from = $derived(data.total ? (data.query.page - 1) * data.pageSize + 1 : 0);
	const to = $derived(Math.min(data.total, data.query.page * data.pageSize));
</script>

<svelte:head><title>Credit notes · RosterChief</title></svelte:head>

{#if data.error}<Alert kind="error">{data.error}</Alert>{/if}

<div class="card">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
		<div class="relative w-full sm:w-80">
			<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" />
			<input class="input input-bordered w-full pl-9" type="search" placeholder="Club, credit note, invoice or replacement invoice number" aria-label="Search credit notes" bind:value={q} oninput={search} />
		</div>
		<select class="select select-bordered w-full sm:w-auto sm:max-w-52" aria-label="Club" value={data.query.club} onchange={(e) => go({ club: e.currentTarget.value })}>
			<option value="">All clubs</option>
			{#each data.clubs as club (club.id)}<option value={club.id}>{club.name}</option>{/each}
		</select>
		<span class="ml-auto font-mono text-[11px] text-muted">{data.total} credit note{data.total === 1 ? '' : 's'}</span>
	</div>

	{#if data.notes.length}
		<div class="overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th>Number</th>
						<th>Type</th>
						<th>Date</th>
						<th>Club</th>
						<th>Invoice</th>
						<th class="text-right">Net</th>
						<th class="text-right">VAT</th>
						<th class="text-right">Total</th>
						<th>Reason</th>
						<th>Applied to</th>
					</tr>
				</thead>
				<tbody>
					{#each data.notes as n (n.id)}
						<tr>
							<td class="whitespace-nowrap"><CreditNotePdf id={n.id} number={n.number} /></td>
							<td class="whitespace-nowrap">
								{#if n.kind === 'void'}<span class="badge badge-error badge-sm badge-outline">Invoice voided</span>{:else}<span class="badge badge-warning badge-sm badge-outline">Plan change</span>{/if}
							</td>
							<td class="font-mono text-xs whitespace-nowrap">{day(n.issued_at)}</td>
							<td><a class="font-semibold text-ink hover:underline" href="/clubs/{n.club.id}">{n.club.name}</a></td>
							<td class="font-mono text-xs whitespace-nowrap"><a class="link" href="/billing?status=all&q={encodeURIComponent(n.invoice_number)}" title="Open the period in Billing">{n.invoice_number}</a>
								{#if n.kind === 'void' && n.replacement_invoice_number}<span class="block text-[11px] text-muted">replaced by <a class="link" href="/billing?status=all&q={encodeURIComponent(n.replacement_invoice_number)}">{n.replacement_invoice_number}</a></span>{/if}
							</td>
							<td class="text-right font-mono tabular-nums">{money2(n.net)}</td>
							<td class="text-right font-mono whitespace-nowrap tabular-nums">
								{money2(n.vat)}
								<span class="block text-[10px] text-muted">{#if n.reverse_charge}<span class="badge badge-ghost badge-xs">reverse charge</span>{:else}VAT {vatPct(n.vat_rate)}{/if}</span>
							</td>
							<td class="text-right font-mono tabular-nums">{money2(n.total)}</td>
							<td class="max-w-64 text-xs text-muted">{n.reason}</td>
							<td class="text-xs">
								{#each n.applied_to as a (a.due_id)}
									<div class="font-mono whitespace-nowrap">{a.invoice_number ?? 'a period'} <span class="text-muted">· {money2(a.amount)}</span></div>
								{:else}
									<span class="text-muted">—</span>{#if n.kind === 'void'}<span class="block text-[11px] text-muted">Document only: no club credit</span>{/if}
								{/each}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="py-10 text-center text-sm text-muted">
			{#if filtered}No credit notes match these filters. <a class="link font-semibold" href="/billing/credit-notes">Show all credit notes</a>.{:else}No credit notes yet. They are issued when a billed club switches plan mid-cycle or an invoice is voided and reissued.{/if}
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
