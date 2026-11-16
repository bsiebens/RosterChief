<script lang="ts">
	import { ChevronRight, ScrollText } from '@lucide/svelte';
	import { actionLabel } from '#lib/admins.ts';
	import { withZone } from '#lib/time.ts';
	import type { UserDetail } from '#lib/users.ts';

	let { entries, userId }: { entries: UserDetail['recent_activity']; userId: string } = $props();

	let open = $state<Record<string, boolean>>({});

	const reasonOf = (d: Record<string, unknown>) => (typeof d?.reason === 'string' && d.reason ? d.reason : '');

	// Readable key labels for the details we know; anything else is shown as its key.
	const LABEL: Record<string, string> = {
		removed: 'Removed',
		notified: 'User notified',
		sessions_ended: 'Sessions ended',
		invalidate_sessions: 'Signed out everywhere',
		disable_current_password: 'Old password disabled',
		is_staff: 'Staff',
		is_superuser: 'Superuser',
		new_account: 'New account',
		plan: 'Plan',
		fields: 'Fields',
		name: 'Name',
		old_number: 'Old number',
		new_number: 'New number',
		credit_note: 'Credit note',
		custom_number: 'Custom number',
		from: 'From',
		to: 'To'
	};
	const fmt = (v: unknown): string =>
		v === null || v === undefined || v === '' ? '—' : typeof v === 'boolean' ? (v ? 'yes' : 'no') : Array.isArray(v) ? v.map(fmt).join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v);
	const details = (d: Record<string, unknown>) => Object.entries(d ?? {}).filter(([k]) => k !== 'reason');

	function concerns(e: UserDetail['recent_activity'][number]) {
		if (e.target_type === 'user') return e.target_id === userId ? 'This account' : 'Another account';
		return e.target_type ? e.target_type : '—';
	}
	// The user as actor vs. as subject: makes it obvious why an entry is listed.
	const role = (e: UserDetail['recent_activity'][number]) => (e.target_type === 'user' && e.target_id === userId ? 'done to this account' : 'done by this account');
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			<ScrollText size={16} aria-hidden="true" /> Recent activity
			<span class="badge badge-ghost ml-1">{entries.length}</span>
			<a class="link ml-auto text-xs font-semibold" href="/admins?target=user">Open the audit log</a>
		</div>
		<p class="-mt-1 text-xs text-muted">The latest 20 recorded entries where this account was the subject (reset, blocked, …) or the one acting (billing, plan changes, …).</p>
	</div>

	{#if entries.length}
		<!-- Fixed layout with wrapping cells: the table always fits the card, no horizontal scrolling. -->
		<table class="table table-fixed">
			<thead>
				<tr><th class="w-8"></th><th class="w-32">When</th><th class="w-[16%]">Action</th><th class="w-[16%]">Done by · concerns</th><th class="w-[12%]">Reason</th><th>Details</th></tr>
			</thead>
			<tbody>
				{#each entries as e (e.id)}
					{@const rows = details(e.data)}
					<tr class="align-top">
						<td>
							{#if rows.length || reasonOf(e.data)}
								<button class="text-dim" aria-expanded={!!open[e.id]} aria-label="Show raw data of {actionLabel(e.action)}" onclick={() => (open[e.id] = !open[e.id])}>
									<ChevronRight size={14} class="transition-transform {open[e.id] ? 'rotate-90' : ''}" aria-hidden="true" />
								</button>
							{/if}
						</td>
						<td class="font-mono text-xs break-words text-muted">{withZone(e.created, true)}</td>
						<td>
							<div class="font-semibold break-words text-ink">{actionLabel(e.action)}</div>
							<div class="font-mono text-[10px] break-all text-dim">{e.action}</div>
							<div class="mt-0.5 text-[11px] {role(e) === 'done to this account' ? 'font-semibold text-warn-text' : 'text-muted'}">{role(e)}</div>
						</td>
						<td class="text-xs">
							<div class="font-mono break-all">{e.actor_email ?? 'system'}</div>
							<div class="text-muted">
								{concerns(e)}
								{#if e.target_type === 'user' && e.target_id !== userId}<a class="link ml-1 font-mono" href="/users/{e.target_id}">open</a>{/if}
							</div>
						</td>
						<td class="text-xs break-words text-slate">{reasonOf(e.data) || '—'}</td>
						<td class="text-xs">
							{#if rows.length}
								<dl class="flex flex-col gap-0.5">
									{#each rows.slice(0, 5) as [k, v] (k)}
										<div class="flex gap-2"><dt class="w-32 shrink-0 text-muted">{LABEL[k] ?? k}</dt><dd class="min-w-0 font-mono break-words text-ink">{fmt(v)}</dd></div>
									{/each}
								</dl>
								{#if rows.length > 5}<div class="mt-0.5 text-muted">+{rows.length - 5} more: expand the row to see everything</div>{/if}
							{:else}<span class="text-dim">—</span>{/if}
						</td>
					</tr>
					{#if open[e.id]}
						<tr class="bg-subhead">
							<td></td>
							<td colspan="5"><pre class="max-h-56 overflow-auto rounded border border-edge bg-white p-3 font-mono text-[11px] whitespace-pre-wrap text-ink select-text">{JSON.stringify(e.data, null, 2)}</pre></td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	{:else}
		<div class="card-body pt-0"><p class="text-sm text-muted">No recorded activity for this account.</p></div>
	{/if}
</div>
