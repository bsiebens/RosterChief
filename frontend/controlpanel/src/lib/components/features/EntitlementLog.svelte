<script lang="ts">
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { withZone } from '#lib/time.ts';
	import type { ClubOption, LogEntry } from '#lib/features.ts';
	import Alert from '../Alert.svelte';

	let { log, total, error, clubs, club, pageNo, pageSize }: { log: LogEntry[]; total: number; error: string; clubs: ClubOption[]; club: string; pageNo: number; pageSize: number } = $props();

	const clubName = $derived(Object.fromEntries(clubs.map((c) => [c.id, c.name])));
	const pages = $derived(Math.max(1, Math.ceil(total / pageSize)));
	const from = $derived(total ? (pageNo - 1) * pageSize + 1 : 0);
	const to = $derived(Math.min(total, pageNo * pageSize));

	function href(over: { club?: string; page?: number }) {
		const p = new URLSearchParams(page.url.searchParams.toString());
		const c = over.club ?? club;
		const n = over.page ?? pageNo;
		c ? p.set('club', c) : p.delete('club');
		n > 1 ? p.set('page', String(n)) : p.delete('page');
		const q = p.toString();
		return `${page.url.pathname}${q ? `?${q}` : ''}#log`;
	}

	const show = (v: Record<string, unknown> | null) =>
		v && Object.keys(v).length
			? Object.entries(v)
					.map(([k, x]) => `${k.replaceAll('_', ' ')}: ${x === null ? 'unlimited' : typeof x === 'object' ? JSON.stringify(x) : x}`)
					.join(', ')
			: '';
	const label = (a: string) => a.replaceAll('_', ' ').replaceAll('.', ' · ');
</script>

<div class="card" id="log">
	<div class="card-body gap-3 pb-3">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<div class="card-title">Entitlement log</div>
				<p class="text-sm text-muted">Every change to plans, subscriptions and club overrides, newest first.</p>
			</div>
			<div class="form-control">
				<label class="label py-0" for="log-club"><span class="label-text">Club</span></label>
				<select id="log-club" class="select select-bordered select-sm w-full sm:min-w-56" value={club} onchange={(e) => goto(href({ club: e.currentTarget.value, page: 1 }), { reset: false, replaceState: true })}>
					<option value="">All clubs</option>
					{#each clubs as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
				</select>
			</div>
		</div>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
	</div>
	<div class="overflow-x-auto">
		<table class="table">
			<thead><tr><th>When</th><th>What</th><th>Club</th><th>By</th><th>Reason</th></tr></thead>
			<tbody>
				{#each log as e (e.id)}
					<tr>
						<td class="font-mono text-[11px] whitespace-nowrap">{withZone(e.created)}</td>
						<td class="min-w-56">
							<span class="badge">{label(e.action)}</span>
							{#if e.plan_code}<span class="ml-1 font-mono text-[11px] text-muted">plan {e.plan_code}</span>{/if}
							{#if e.feature_code}<span class="ml-1 font-mono text-[11px] text-muted">{e.feature_code}</span>{/if}
							{#if show(e.old) || show(e.new)}
								<div class="mt-1 text-xs text-muted">{#if show(e.old)}<span>{show(e.old)}</span> → {/if}<span class="text-ink">{show(e.new) || '—'}</span></div>
							{/if}
						</td>
						<td class="text-sm">{#if e.club_id}<a class="link" href="/clubs/{e.club_id}">{clubName[e.club_id] ?? 'Club'}</a>{:else}<span class="text-muted">platform</span>{/if}</td>
						<td class="text-xs">{e.actor_email ?? 'system'}</td>
						<td class="max-w-64 text-xs text-muted">{e.reason || '—'}</td>
					</tr>
				{:else}
					<tr><td colspan="5" class="text-sm text-muted">{error ? 'The log could not be loaded.' : club ? 'No changes recorded for this club.' : 'No changes recorded yet.'}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if total > pageSize}
		<div class="flex items-center justify-between border-t border-line px-4 py-2 font-mono text-[11px] text-muted">
			<span>{from}–{to} of {total}</span>
			<nav class="flex items-center gap-2" aria-label="Log pages">
				<a class="btn btn-outline btn-xs {pageNo <= 1 ? 'pointer-events-none opacity-40' : ''}" href={href({ page: pageNo - 1 })} aria-label="Previous page" aria-disabled={pageNo <= 1}><ChevronLeft size={12} aria-hidden="true" /></a>
				<span>page {pageNo} / {pages}</span>
				<a class="btn btn-outline btn-xs {pageNo >= pages ? 'pointer-events-none opacity-40' : ''}" href={href({ page: pageNo + 1 })} aria-label="Next page" aria-disabled={pageNo >= pages}><ChevronRight size={12} aria-hidden="true" /></a>
			</nav>
		</div>
	{/if}
</div>
