<script lang="ts">
	import { SlidersHorizontal } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubDetail, Entitlement, Override } from '#lib/clubs.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { club, entitlements, overrides, canEdit, onchange }: { club: ClubDetail; entitlements: Entitlement[]; overrides: Override[]; canEdit: boolean; onchange: () => void } = $props();

	const overrideOf = $derived(Object.fromEntries(overrides.map((o) => [o.feature_code, o])));

	let editing = $state<Entitlement | null>(null);
	let enabled = $state('true');
	let limit = $state('');
	let expires = $state('');
	let reason = $state('');
	let error = $state('');
	let notice = $state('');
	let busy = $state(false);

	function edit(e: Entitlement) {
		const o = overrideOf[e.code];
		editing = e;
		enabled = String(o ? o.enabled : !e.enabled);
		limit = o?.limit != null ? String(o.limit) : e.limit != null ? String(e.limit) : '';
		expires = o?.expires_at ? o.expires_at.slice(0, 10) : '';
		reason = o?.reason ?? '';
		error = '';
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!editing) return;
		busy = true;
		error = '';
		const body: Record<string, unknown> = { enabled: enabled === 'true', reason: reason.trim() };
		if (editing.kind === 'limit' && limit !== '') body.limit = Number(limit);
		if (expires) body.expires_at = new Date(`${expires}T23:59:00`).toISOString();
		const r = await platform('PUT', `/clubs/${club.id}/overrides/${editing.code}`, body);
		busy = false;
		if (!r.ok) return (error = r.fields.limit ?? r.message ?? 'The override could not be saved.');
		notice = `Override saved for “${editing.name}”.`;
		editing = null;
		onchange();
	}

	async function clear(e: Entitlement) {
		busy = true;
		const r = await platform('DELETE', `/clubs/${club.id}/overrides/${e.code}`);
		busy = false;
		if (!r.ok) return (notice = r.message ?? 'Could not remove the override.');
		notice = `Override removed for “${e.name}”. The plan applies again.`;
		onchange();
	}

	const source = { plan: 'plan', default_plan: 'default plan', overdue: 'free plan (unpaid)', override: 'override', none: 'not included' } as const;
	const date = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title"><SlidersHorizontal size={16} aria-hidden="true" /> Features</div>
		<p class="-mt-1 text-sm text-muted">What this club can use right now. An override grants or revokes one feature on top of the plan, optionally until a date.</p>
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
	</div>
	<div class="overflow-x-auto">
		<table class="table">
			<thead>
				<tr><th>Feature</th><th>Access</th><th>Source</th><th>Override</th><th></th></tr>
			</thead>
			<tbody>
				{#each entitlements as e (e.code)}
					{@const o = overrideOf[e.code]}
					<tr>
						<td>
							<div class="font-semibold text-ink">{e.name}</div>
							<div class="font-mono text-[11px] text-muted">{e.code}</div>
						</td>
						<td>
							{#if e.enabled}
								<span class="badge badge-success">{e.kind === 'limit' ? (e.limit == null ? 'unlimited' : `limit ${e.limit}`) : 'on'}</span>
							{:else}
								<span class="badge badge-ghost">off</span>
							{/if}
						</td>
						<td class="font-mono text-xs {e.source === 'override' || e.source === 'overdue' ? 'font-bold text-warn-text' : 'text-muted'}">{source[e.source]}</td>
						<td class="max-w-64 text-xs text-muted">
							{#if o}
								<div class="text-ink">{o.enabled ? 'granted' : 'revoked'}{o.limit != null ? ` · limit ${o.limit}` : ''}{o.expires_at ? ` · until ${date(o.expires_at)}` : ''}{o.is_current ? '' : ' · expired'}</div>
								{#if o.reason}<div class="truncate" title={o.reason}>{o.reason}</div>{/if}
							{:else}—{/if}
						</td>
						<td class="text-right whitespace-nowrap">
							{#if canEdit}
								<button class="btn btn-outline btn-xs" onclick={() => edit(e)}>{o ? 'Edit' : 'Override'}</button>
								{#if o}<button class="btn btn-ghost btn-xs text-club-dark hover:bg-danger-bg" disabled={busy} onclick={() => clear(e)}>Remove</button>{/if}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<Modal open={!!editing} title={editing ? `Override · ${editing.name}` : 'Override'} onclose={() => (editing = null)}>
	{#if editing}
		<form class="flex flex-col gap-4" onsubmit={save}>
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="form-control">
				<label class="label" for="ov-enabled"><span class="label-text">For this club</span></label>
				<select id="ov-enabled" class="select select-bordered w-full" bind:value={enabled}>
					<option value="true">Grant the feature</option>
					<option value="false">Revoke the feature</option>
				</select>
			</div>
			{#if editing.kind === 'limit' && enabled === 'true'}
				<div class="form-control">
					<label class="label" for="ov-limit"><span class="label-text">Limit <span class="text-muted">(empty = unlimited)</span></span></label>
					<input id="ov-limit" class="input input-bordered w-full" type="number" min="0" bind:value={limit} />
				</div>
			{/if}
			<div class="form-control">
				<label class="label" for="ov-expires"><span class="label-text">Until <span class="text-muted">(optional)</span></span></label>
				<input id="ov-expires" class="input input-bordered w-full" type="date" bind:value={expires} />
			</div>
			<div class="form-control">
				<label class="label" for="ov-reason"><span class="label-text">Reason <span class="text-muted">(kept in the entitlement log)</span></span></label>
				<input id="ov-reason" class="input input-bordered w-full" maxlength="200" bind:value={reason} />
			</div>
			<div class="modal-action mt-0">
				<button type="button" class="btn btn-outline" onclick={() => (editing = null)}>Cancel</button>
				<button type="submit" class="btn btn-primary" disabled={busy}>Save override</button>
			</div>
		</form>
	{/if}
</Modal>
