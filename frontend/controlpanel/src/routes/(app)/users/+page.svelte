<script lang="ts">
	import { goto } from '$app/navigation';
	import { navigating } from '$app/state';
	import { Check, ChevronLeft, ChevronRight, Search } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { withZone, zoneLabel } from '#lib/time.ts';
	import { userName } from '#lib/users.ts';

	let { data } = $props();

	let q = $state('');
	$effect(() => {
		q = data.query.q;
	});

	function href(over: Record<string, string | number>) {
		const p = new URLSearchParams();
		const merged: Record<string, string | number> = { ...data.query, ...over };
		const defaults: Record<string, string | number> = { q: '', status: 'all', mfa: 'any', level: 'any', page: 1 };
		for (const [k, v] of Object.entries(merged)) if (v !== defaults[k]) p.set(k, String(v));
		const s = p.toString();
		return `/users${s ? `?${s}` : ''}`;
	}
	const go = (over: Record<string, string | number>) => goto(href(over), { reset: false, replaceState: true });

	let timer: ReturnType<typeof setTimeout>;
	function search() {
		clearTimeout(timer);
		timer = setTimeout(() => go({ q: q.trim(), page: 1 }), 300);
	}

	const loading = $derived(!!navigating.to);
	const filtered = $derived(!!(data.query.q || data.query.status !== 'all' || data.query.mfa !== 'any' || data.query.level !== 'any'));
	const pages = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const from = $derived(data.total ? (data.query.page - 1) * data.pageSize + 1 : 0);
	const to = $derived(Math.min(data.total, data.query.page * data.pageSize));
	const zone = zoneLabel();
</script>

<svelte:head><title>Users · RosterChief</title></svelte:head>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Users</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">Find any account to inspect it, reset two-factor or password, or block it.</p>
</div>

{#if data.error}<Alert kind="error">{data.error}</Alert>{/if}

<div class="card">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 lg:flex-nowrap">
		<div class="relative w-full lg:max-w-sm lg:flex-1">
			<Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" />
			<!-- svelte-ignore a11y_autofocus -->
			<input class="input input-bordered w-full pl-9" type="search" autofocus placeholder="Search by email or name" aria-label="Search users" bind:value={q} oninput={search} />
		</div>
		<select class="select select-bordered w-full sm:w-auto" aria-label="Status" value={data.query.status} onchange={(e) => go({ status: e.currentTarget.value, page: 1 })}>
			<option value="all">Status: all</option>
			<option value="active">Status: active</option>
			<option value="blocked">Status: blocked</option>
		</select>
		<select class="select select-bordered w-full sm:w-auto" aria-label="Two-factor" value={data.query.mfa} onchange={(e) => go({ mfa: e.currentTarget.value, page: 1 })}>
			<option value="any">2FA: any</option>
			<option value="yes">2FA: enabled</option>
			<option value="no">2FA: not enabled</option>
		</select>
		<select class="select select-bordered w-full sm:w-auto" aria-label="Platform level" value={data.query.level} onchange={(e) => go({ level: e.currentTarget.value, page: 1 })}>
			<option value="any">Level: any</option>
			<option value="superuser">Level: superuser</option>
			<option value="staff">Level: staff</option>
			<option value="none">Level: none</option>
		</select>
	</div>

	<div class="overflow-x-auto transition-opacity {loading ? 'opacity-50' : ''}" aria-busy={loading}>
		<table class="table">
			<thead><tr><th>User</th><th>Status</th><th>Level</th><th>2FA</th><th>Email</th><th class="text-right">Clubs</th><th>Last sign-in{zone ? ` (${zone})` : ''}</th></tr></thead>
			<tbody>
				{#each data.users as u (u.id)}
					{@const name = userName(u)}
					<tr class={u.blocked ? 'bg-danger-bg' : ''}>
						<td>
							<a class="block" href="/users/{u.id}">
								<span class="block font-semibold text-ink hover:underline">{name || u.email}</span>
								{#if name}<span class="block font-mono text-[11px] text-muted">{u.email}</span>{/if}
							</a>
						</td>
						<td><span class="badge badge-sm {u.blocked ? 'badge-error' : 'badge-success'}">{u.blocked ? 'blocked' : 'active'}</span></td>
						<td>
							{#if u.platform_level}<span class="badge badge-sm {u.platform_level === 'superuser' ? 'badge-neutral' : 'badge-info'}">{u.platform_level}</span>{:else}<span class="text-dim">—</span>{/if}
						</td>
						<td>
							{#if u.mfa_enabled}<span class="badge badge-success badge-sm">on</span>
							{:else if u.platform_level}<span class="badge badge-warning badge-sm" title="Platform staff need two-factor">off</span>
							{:else}<span class="badge badge-ghost badge-sm">off</span>{/if}
						</td>
						<td>
							{#if u.email_verified}<span class="inline-flex items-center gap-1 text-xs text-muted"><Check size={13} aria-hidden="true" /> verified</span>{:else}<span class="badge badge-warning badge-sm">unverified</span>{/if}
						</td>
						<td class="text-right font-mono text-xs">{u.club_count}</td>
						<td class="font-mono text-xs whitespace-nowrap text-muted">{u.last_login ? withZone(u.last_login) : 'never'}</td>
					</tr>
				{:else}
					<tr>
						<td colspan="7" class="py-10 text-center text-sm text-muted">
							{#if loading}Loading…
							{:else if data.error}Users could not be loaded.
							{:else if filtered}No users match these filters. Try fewer words or clear a filter.
							{:else}No users found.{/if}
							{#if !filtered && !loading}<span class="block pt-1">Type part of an email address or a name; every word must match.</span>{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if data.total > 0}
		<div class="flex items-center justify-between border-t border-line px-4 py-2.5 font-mono text-[11px] text-muted">
			<span>{from}–{to} of {data.total}</span>
			{#if data.total > data.pageSize}
				<div class="flex items-center gap-2">
					<a class="btn btn-outline btn-xs {data.query.page <= 1 ? 'pointer-events-none opacity-40' : ''}" href={href({ page: data.query.page - 1 })} aria-label="Previous page"><ChevronLeft size={12} aria-hidden="true" /></a>
					<span>page {data.query.page} / {pages}</span>
					<a class="btn btn-outline btn-xs {data.query.page >= pages ? 'pointer-events-none opacity-40' : ''}" href={href({ page: data.query.page + 1 })} aria-label="Next page"><ChevronRight size={12} aria-hidden="true" /></a>
				</div>
			{/if}
		</div>
	{/if}
</div>
