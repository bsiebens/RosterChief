<script lang="ts">
	import { goto } from '$app/navigation';
	import { ChevronLeft, ChevronRight, Code, ScrollText } from '@lucide/svelte';
	import { ACTIONS, TARGET_TYPES, actionLabel, type AuditEntry, type AuditQuery, type PlatformAdmin } from '#lib/admins.ts';
	import { withZone, zoneLabel } from '#lib/time.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let {
		entries,
		total,
		error,
		query,
		pageSize,
		admins,
		clubs
	}: { entries: AuditEntry[]; total: number; error: string; query: AuditQuery; pageSize: number; admins: PlatformAdmin[]; clubs: { id: string; name: string }[] } = $props();

	let viewing = $state<AuditEntry | null>(null);
	let viewOpen = $state(false);
	$effect(() => {
		viewOpen = !!viewing;
	});

	const clubName = $derived(new Map(clubs.map((c) => [c.id, c.name])));
	const actions = $derived(query.action && !(ACTIONS as readonly string[]).includes(query.action) ? [...ACTIONS, query.action] : ACTIONS);
	const targets = $derived(query.target && !TARGET_TYPES.includes(query.target) ? [...TARGET_TYPES, query.target] : TARGET_TYPES);
	const pages = $derived(Math.max(1, Math.ceil(total / pageSize)));
	const from = $derived(total ? (query.page - 1) * pageSize + 1 : 0);
	const to = $derived(Math.min(total, query.page * pageSize));
	const filtered = $derived(!!(query.action || query.actor || query.club || query.target));

	function href(over: Partial<AuditQuery>) {
		const p = new URLSearchParams();
		for (const [k, v] of Object.entries({ ...query, ...over })) if (v !== '' && !(k === 'page' && Number(v) === 1)) p.set(k, String(v));
		const s = p.toString();
		return `/admins${s ? `?${s}` : ''}`;
	}
	const set = (key: keyof AuditQuery, value: string) => goto(href({ [key]: value, page: 1 }), { reset: false, replaceState: true });

	const zone = zoneLabel();
	const short = (id: string) => (id.length > 12 ? `${id.slice(0, 8)}…` : id);
</script>

<section class="card" aria-labelledby="audit-h">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
		<h2 id="audit-h" class="card-title m-0"><ScrollText size={16} aria-hidden="true" /> Audit log <span class="badge badge-ghost ml-1">{total}</span></h2>
	</div>
	<div class="grid gap-3 border-b border-line px-4 py-3 sm:grid-cols-2 lg:grid-cols-4">
		<select class="select select-bordered w-full" aria-label="Action" value={query.action} onchange={(e) => set('action', e.currentTarget.value)}>
			<option value="">All actions</option>
			{#each actions as a (a)}<option value={a}>{actionLabel(a)} ({a})</option>{/each}
		</select>
		<select class="select select-bordered w-full" aria-label="Actor" value={query.actor} onchange={(e) => set('actor', e.currentTarget.value)}>
			<option value="">All actors</option>
			{#each admins as a (a.id)}<option value={a.id}>{a.email}</option>{/each}
		</select>
		<select class="select select-bordered w-full" aria-label="Club" value={query.club} onchange={(e) => set('club', e.currentTarget.value)}>
			<option value="">All clubs</option>
			{#each clubs as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
		</select>
		<select class="select select-bordered w-full" aria-label="Target type" value={query.target} onchange={(e) => set('target', e.currentTarget.value)}>
			<option value="">All target types</option>
			{#each targets as t (t)}<option value={t}>{t}</option>{/each}
		</select>
	</div>

	{#if error}<div class="p-4"><Alert kind="error">{error}</Alert></div>{/if}

	<div class="overflow-x-auto">
		<table class="table">
			<thead><tr><th>Time{zone ? ` (${zone})` : ''}</th><th>Actor</th><th>Action</th><th>Target</th><th>Club</th><th class="text-right">Data</th></tr></thead>
			<tbody>
				{#each entries as e (e.id)}
					<tr>
						<td class="font-mono text-xs whitespace-nowrap text-slate">{withZone(e.created, true)}</td>
						<td class="font-mono text-xs text-muted">{e.actor_email ?? 'system'}</td>
						<td>
							<span class="block text-sm font-semibold text-ink">{actionLabel(e.action)}</span>
							<span class="block font-mono text-[11px] text-muted">{e.action}</span>
						</td>
						<td class="font-mono text-xs">{#if e.target_type}{e.target_type}{#if e.target_type === 'user'}<a class="text-muted hover:underline" href="/users/{e.target_id}" title={e.target_id}> {short(e.target_id)}</a>{:else}<span class="text-muted" title={e.target_id}> {short(e.target_id)}</span>{/if}{:else}<span class="text-dim">—</span>{/if}</td>
						<td class="text-sm">{#if e.club_id}<a class="link font-semibold" href="/clubs/{e.club_id}">{clubName.get(e.club_id) ?? short(e.club_id)}</a>{:else}<span class="text-dim">—</span>{/if}</td>
						<td class="text-right">
							<button class="btn btn-outline btn-xs" onclick={() => (viewing = e)} aria-label="View data of {actionLabel(e.action)}" disabled={!Object.keys(e.data).length}><Code size={12} aria-hidden="true" /> JSON</button>
						</td>
					</tr>
				{:else}
					<tr><td colspan="6" class="py-10 text-center text-sm text-muted">{filtered ? 'No audit entries match these filters.' : 'Nothing has been logged yet.'}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if total > pageSize}
		<div class="flex items-center justify-between border-t border-line px-4 py-2.5 font-mono text-[11px] text-muted">
			<span>{from}–{to} of {total}</span>
			<div class="flex items-center gap-2">
				<a class="btn btn-outline btn-xs {query.page <= 1 ? 'pointer-events-none opacity-40' : ''}" href={href({ page: query.page - 1 })} aria-label="Previous page"><ChevronLeft size={12} aria-hidden="true" /></a>
				<span>page {query.page} / {pages}</span>
				<a class="btn btn-outline btn-xs {query.page >= pages ? 'pointer-events-none opacity-40' : ''}" href={href({ page: query.page + 1 })} aria-label="Next page"><ChevronRight size={12} aria-hidden="true" /></a>
			</div>
		</div>
	{/if}
</section>

<Modal bind:open={viewOpen} title={viewing ? actionLabel(viewing.action) : 'Audit entry'} onclose={() => (viewing = null)}>
	{#if viewing}
		<p class="mb-2 font-mono text-[11px] text-muted">{viewing.action} · {withZone(viewing.created, true)} · {viewing.actor_email ?? 'system'}</p>
		<pre class="max-h-96 overflow-auto rounded border border-line bg-subhead p-3 font-mono text-xs break-words whitespace-pre-wrap">{JSON.stringify(viewing.data, null, 2)}</pre>
		<div class="modal-action"><button class="btn btn-outline" onclick={() => (viewing = null)}>Close</button></div>
	{/if}
</Modal>
