<script lang="ts">
	import { goto, refreshAll } from '$app/navigation';
	import { onMount } from 'svelte';
	import { ChevronLeft, ChevronRight, Plus, Search, Upload } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import Alert from '#lib/components/Alert.svelte';

	let { data } = $props();

	let q = $state('');
	$effect(() => {
		q = data.query.q;
	});

	const STATUSES = [
		['active', 'Active'],
		['archived', 'Archived'],
		['all', 'All']
	] as const;

	function href(over: Record<string, string | number | null>) {
		const p = new URLSearchParams();
		const merged = { q: data.query.q, status: data.query.status, sport: data.query.sport, page: data.query.page, ...over };
		for (const [k, v] of Object.entries(merged)) if (v !== '' && v !== null && !(k === 'status' && v === 'active') && !(k === 'page' && Number(v) === 1)) p.set(k, String(v));
		const s = p.toString();
		return `/clubs${s ? `?${s}` : ''}`;
	}

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		timer = setTimeout(() => goto(href({ q: q.trim(), page: 1 }), { reset: false, replaceState: true }), 300);
	}

	// While a club is being built, keep checking so it turns into a normal row by itself.
	const building = $derived(data.requests.some((r) => r.status === 'pending' || r.status === 'running'));
	onMount(() => {
		const t = setInterval(() => building && !document.hidden && refreshAll(), 4000);
		return () => clearInterval(t);
	});

	const pages = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const from = $derived(data.total ? (data.query.page - 1) * data.pageSize + 1 : 0);
	const to = $derived(Math.min(data.total, data.query.page * data.pageSize));
	const date = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
</script>

<svelte:head><title>Clubs · RosterChief</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3">
	<div>
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Clubs</h1>
		<p class="mt-1.5 font-mono text-[11px] text-muted">{data.total} club{data.total === 1 ? '' : 's'}{data.query.status === 'all' ? '' : ` · ${data.query.status}`}</p>
	</div>
	<div class="flex flex-wrap gap-2">
		{#if data.auth.status === 'authenticated' && data.auth.me.level === 'superuser'}
			<a class="btn btn-outline" href="/clubs/restore"><Upload size={14} aria-hidden="true" /> Restore backup</a>
		{/if}
		<a class="btn btn-primary" href="/clubs/new"><Plus size={14} aria-hidden="true" /> New club</a>
	</div>
</div>

{#each data.requests as job (job.id)}
	{#if job.status === 'failed'}
		<Alert kind="error" title="Club creation failed">
			“{job.name}” could not be created{job.error ? `: ${job.error}` : '.'}
			<a class="link font-semibold" href="/clubs/new">Try again</a>
		</Alert>
	{:else}
		<Alert kind="info" title="Creating a club">“{job.name}” is being set up ({job.status}). It appears in the list as soon as it is ready.</Alert>
	{/if}
{/each}

{#if data.error}<Alert kind="error">{data.error}</Alert>{/if}

<div class="card">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
		<div class="relative w-full sm:w-72">
			<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" />
			<input class="input input-bordered w-full pl-9" type="search" placeholder="Search name or slug" aria-label="Search clubs" bind:value={q} oninput={search} />
		</div>
		<select class="select select-bordered w-full sm:w-auto sm:min-w-44" aria-label="Sport" value={data.query.sport} onchange={(e) => goto(href({ sport: e.currentTarget.value, page: 1 }))}>
			<option value="">All sports</option>
			{#each data.sports as sport (sport.id)}<option value={sport.id}>{sport.name}</option>{/each}
		</select>
		<div class="flex-1"></div>
		<div class="flex gap-1" role="group" aria-label="Status">
			{#each STATUSES as [value, label] (value)}
				<a class="btn btn-xs {data.query.status === value ? '' : 'btn-outline'}" href={href({ status: value, page: 1 })} aria-current={data.query.status === value ? 'true' : undefined}>{label}{#if data.counts}<span class="ml-1 font-mono tabular-nums opacity-80" aria-label="{data.counts[value]} clubs">{data.counts[value]}</span>{/if}</a>
			{/each}
		</div>
	</div>

	<div class="overflow-x-auto">
		<table class="table">
			<thead>
				<tr><th>Club</th><th>Sport</th><th>Owner</th><th>Plan</th><th>Created</th><th></th></tr>
			</thead>
			<tbody>
				{#each data.clubs as club (club.id)}
					<tr>
						<td>
							<a class="flex items-center gap-3" href="/clubs/{club.id}">
								<ClubCrest name={club.name} logo={club.logo_url} />
								<span>
									<span class="block font-semibold text-ink hover:underline">{club.name}</span>
									<span class="block font-mono text-[11px] text-muted">{club.slug}</span>
								</span>
							</a>
						</td>
						<td class="text-sm">{club.sport?.name ?? '—'}</td>
						<td class="font-mono text-xs text-muted">{club.owner_email}</td>
						<td class="font-mono text-xs">{club.plan ?? 'default'}</td>
						<td class="font-mono text-xs whitespace-nowrap text-muted">{date(club.created)}</td>
						<td class="text-right">
							{#if club.archived_at}<span class="badge badge-warning badge-sm">archived</span>{/if}
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="6" class="py-10 text-center text-sm text-muted">
							{#if data.query.q || data.query.sport || data.query.status !== 'active'}No clubs match these filters.{:else}No clubs yet. <a class="link font-semibold" href="/clubs/new">Create the first one</a>.{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

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
