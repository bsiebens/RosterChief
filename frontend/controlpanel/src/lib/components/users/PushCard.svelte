<script lang="ts">
	import { Bell, Users } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { STATUS_BADGE, providerLabel, type UserFollowing, type UserPush } from '#lib/push.ts';
	import { withZone } from '#lib/time.ts';

	let { userId, push, error, ondone }: { userId: string; push: UserPush | null; error: string; ondone: (message: string) => void } = $props();

	type Removal = { kind: 'device' | 'web'; id: string; label: string };
	let removal = $state<Removal | null>(null);
	let open = $state(false);
	let busy = $state(false);
	let failure = $state('');

	function ask(r: Removal) {
		removal = r;
		failure = '';
		open = true;
	}

	async function remove() {
		if (!removal) return;
		busy = true;
		failure = '';
		const path = removal.kind === 'device' ? `devices/${removal.id}` : `web-subscriptions/${removal.id}`;
		const r = await platform('DELETE', `/users/${userId}/push/${path}`);
		busy = false;
		if (!r.ok) return (failure = r.message ?? 'It could not be removed.');
		open = false;
		ondone(removal.kind === 'device' ? 'Device removed.' : 'Web subscription removed.');
	}

	// -- the teams (and single games) this account follows for live game pushes; loaded when the pop-up opens --
	let followOpen = $state(false);
	let following = $state<UserFollowing | null>(null);
	let followBusy = $state(false);
	let followError = $state('');
	async function showFollowing() {
		followOpen = true;
		followBusy = true;
		followError = '';
		const r = await platform<UserFollowing>('GET', `/users/${userId}/push/following`);
		followBusy = false;
		if (r.ok && r.data) following = r.data;
		else followError = r.message ?? 'The followed teams could not be loaded.';
	}

	const prefs = $derived(push?.preferences);
	const clubName = (c: { name: string } | null) => (c ? c.name : 'all clubs');
	const offSwitches = $derived(prefs?.master_switches.filter((s) => !s.enabled) ?? []);
	const quiet = $derived(prefs?.quiet_hours ?? null);
</script>

{#snippet heading(title: string, count?: number)}
	<h3 class="mb-3 flex items-center gap-2 font-mono text-[10px] tracking-[.08em] text-muted uppercase">
		{title}{#if count !== undefined}<span class="badge badge-ghost badge-sm">{count}</span>{/if}
	</h3>
{/snippet}

<div class="card">
	<div class="card-body pb-3">
		<div class="card-title">
			<Bell size={16} aria-hidden="true" /> Push
			<button type="button" class="btn btn-outline btn-xs ml-auto gap-1.5" onclick={showFollowing}><Users size={13} aria-hidden="true" /> Followed teams</button>
		</div>
		{#if error || !push || !prefs}
			<Alert kind="error">{error || 'The push details could not be loaded.'}</Alert>
		{/if}
	</div>

	{#if push && prefs}
		<div class="grid divide-y divide-line lg:grid-cols-3 lg:divide-x lg:divide-y-0">
			<section class="p-4">
				{@render heading('Devices', push.devices.length)}
				{#if push.devices.length}
					<ul class="flex flex-col gap-3">
						{#each push.devices as d (d.id)}
							<li class="rounded border border-line p-3">
								<div class="flex flex-wrap items-center gap-1.5">
									<span class="badge {d.platform === 'ios' ? 'badge-neutral' : 'badge-info'}">{d.platform_label}</span>
									<span class="font-mono text-[10px] text-muted">{providerLabel(d.provider)}</span>
									<span class="badge {d.enabled ? 'badge-success' : 'badge-error'} ml-auto">{d.enabled ? 'enabled' : 'token rejected'}</span>
									{#if d.stale}<span class="badge badge-warning">stale</span>{/if}
								</div>
								<dl class="mt-2 grid grid-cols-[5.5rem_1fr] gap-y-1 text-xs">
									<dt class="text-muted">Environment</dt><dd>{d.environment || '—'}</dd>
									<dt class="text-muted">App version</dt><dd class="font-mono">{d.app_version || '—'}</dd>
									<dt class="text-muted">Locale</dt><dd class="font-mono">{d.locale || '—'}</dd>
									<dt class="text-muted">Last seen</dt><dd class="font-mono">{withZone(d.last_seen)}</dd>
									{#if d.disabled_at}<dt class="text-muted">Disabled</dt><dd class="font-mono">{withZone(d.disabled_at)}</dd>{/if}
								</dl>
								<div class="mt-3 flex gap-2">
									<a class="btn btn-outline btn-xs" href="/push/sandbox?target=device:{d.id}&user={userId}">Send test</a>
									<button class="btn btn-outline btn-error btn-xs ml-auto" onclick={() => ask({ kind: 'device', id: d.id, label: `${d.platform_label} device` })}>Remove</button>
								</div>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="text-sm text-muted">No registered devices. The mobile app registers one the first time the user allows notifications.</p>
				{/if}
			</section>

			<section class="p-4">
				{@render heading('Web subscriptions', push.web_subscriptions.length)}
				{#if push.web_subscriptions.length}
					<ul class="flex flex-col gap-3">
						{#each push.web_subscriptions as w (w.id)}
							<li class="rounded border border-line p-3">
								<div class="text-xs break-words text-ink">{w.user_agent || 'Unknown browser'}</div>
								<dl class="mt-2 grid grid-cols-[5.5rem_1fr] gap-y-1 text-xs">
									{#if w.club}<dt class="text-muted">Club</dt><dd>{w.club.name}</dd>{/if}
									<dt class="text-muted">Endpoint</dt><dd class="font-mono break-all">{w.endpoint_masked}</dd>
									<dt class="text-muted">Last seen</dt><dd class="font-mono">{withZone(w.last_seen)}</dd>
								</dl>
								<div class="mt-3 flex gap-2">
									<a class="btn btn-outline btn-xs" href="/push/sandbox?target=web_subscription:{w.id}&user={userId}">Send test</a>
									<button class="btn btn-outline btn-error btn-xs ml-auto" onclick={() => ask({ kind: 'web', id: w.id, label: 'web subscription' })}>Remove</button>
								</div>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="text-sm text-muted">None.</p>
				{/if}
			</section>

			<section class="p-4">
				{@render heading('Preferences')}
				<dl class="divide-y divide-line text-sm">
					<div class="flex items-start justify-between gap-3 py-2 first:pt-0">
						<dt class="text-muted">Email</dt>
						<dd class="text-right">{prefs.email_mode_label}</dd>
					</div>
					<div class="flex items-start justify-between gap-3 py-2">
						<dt class="text-muted">Quiet hours</dt>
						<dd class="text-right">
							{#if quiet?.enabled}
								<span class="font-mono text-xs">{quiet.start} – {quiet.end}</span>
								<div class="text-xs text-muted">{quiet.timezone}</div>
								{#if quiet.active_now}<span class="badge badge-warning mt-1">active now{quiet.until ? ` until ${withZone(quiet.until)}` : ''}</span>{/if}
							{:else}<span class="text-muted">off</span>{/if}
						</dd>
					</div>
					<div class="flex items-start justify-between gap-3 py-2">
						<dt class="text-muted">Switched off</dt>
						<dd class="text-right">
							{#if offSwitches.length}
								<ul class="flex flex-wrap justify-end gap-1.5">{#each offSwitches as s, i (i)}<li class="badge badge-error">all {s.channel} · {clubName(s.club)}</li>{/each}</ul>
							{:else}<span class="text-muted">nothing</span>{/if}
						</dd>
					</div>
					<div class="flex items-start justify-between gap-3 py-2 last:pb-0">
						<dt class="text-muted">Categories off</dt>
						<dd class="text-right">
							{#if prefs.categories_off.length}
								<ul class="flex flex-wrap justify-end gap-1.5">{#each prefs.categories_off as c, i (i)}<li class="badge badge-ghost">{c.category_label} · {c.channel} · {clubName(c.club)}</li>{/each}</ul>
							{:else}<span class="text-muted">none</span>{/if}
						</dd>
					</div>
				</dl>
			</section>
		</div>

		<section>
			<div class="px-4 pt-4">{@render heading('Recent deliveries', push.recent_deliveries.length)}</div>
			{#if push.recent_deliveries.length}
				<table class="table table-fixed">
					<thead><tr><th class="w-36">When</th><th class="w-32">Provider</th><th class="w-24">Status</th><th class="w-[16%]">Category</th><th>Title</th><th class="w-14">Tries</th></tr></thead>
					<tbody>
						{#each push.recent_deliveries as d (d.id)}
							<tr class="align-top">
								<td class="font-mono text-xs break-words text-muted">{withZone(d.created, true)}</td>
								<td class="text-xs">{providerLabel(d.provider)}</td>
								<td><span class="badge {STATUS_BADGE[d.status] ?? 'badge-ghost'}">{d.status}</span></td>
								<td class="text-xs">{d.category}</td>
								<td class="text-xs break-words">{d.title}{#if d.error}<div class="font-mono text-[11px] text-club-dark">{d.error}</div>{/if}</td>
								<td class="font-mono tabular-nums">{d.attempts}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="px-4 pb-4 text-sm text-muted">No deliveries yet.</p>
			{/if}
		</section>
	{/if}
</div>

<Modal bind:open={followOpen} title="Followed teams and games">
	<div class="flex flex-col gap-4">
		<p class="text-sm text-muted">Who gets live game pushes: the teams (and single games) this account follows, per club. Following is the user's own choice in the app.</p>
		{#if followBusy}
			<p class="text-sm text-muted">Loading…</p>
		{:else if followError}
			<Alert kind="error">{followError}</Alert>
		{:else if following && !following.clubs.length}
			<p class="text-sm">This account follows no teams or games.</p>
		{:else if following}
			<ul class="flex flex-col gap-4">
				{#each following.clubs as c (c.club.id)}
					<li class="rounded border border-line p-3">
						<h4 class="mb-2 font-semibold text-ink">{c.club.name}</h4>
						{#if c.teams.length}
							<p class="mb-1 font-mono text-[10px] tracking-[.08em] text-muted uppercase">Teams · {c.teams.length}</p>
							<ul class="mb-2 flex flex-wrap gap-1.5">{#each c.teams as t (t.id)}<li class="badge badge-neutral">{t.name}</li>{/each}</ul>
						{/if}
						{#if c.games.length}
							<p class="mb-1 font-mono text-[10px] tracking-[.08em] text-muted uppercase">Single games · {c.games.length}</p>
							<ul class="text-sm">{#each c.games as g (g.id)}<li class="flex justify-between gap-3 py-0.5"><span>{g.title}</span><span class="font-mono text-xs text-muted">{withZone(g.start)}</span></li>{/each}</ul>
						{/if}
					</li>
				{/each}
			</ul>
			<p class="text-xs text-muted">{following.total_teams} team{following.total_teams === 1 ? '' : 's'} and {following.total_games} single game{following.total_games === 1 ? '' : 's'} in total.</p>
		{/if}
		<div class="modal-action mt-0"><button type="button" class="btn btn-outline" onclick={() => (followOpen = false)}>Close</button></div>
	</div>
</Modal>

<Modal bind:open title={removal?.kind === 'web' ? 'Remove web subscription' : 'Remove device'}>
	<div class="flex flex-col gap-4">
		{#if removal?.kind === 'web'}
			<p class="text-sm">The browser stops receiving pushes for this account. It subscribes again the next time the user allows notifications in that browser.</p>
		{:else}
			<p class="text-sm">The {removal?.label} is removed and stops receiving pushes. The app registers it again the next time the user opens it, so this is safe if the device is wrongly flagged.</p>
		{/if}
		{#if failure}<Alert kind="error">{failure}</Alert>{/if}
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)}>Cancel</button>
			<button type="button" class="btn btn-outline btn-error" disabled={busy} onclick={remove}>Remove</button>
		</div>
	</div>
</Modal>
