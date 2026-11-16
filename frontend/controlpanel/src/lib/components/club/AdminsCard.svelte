<script lang="ts">
	import { Crown, Trash2, UserPlus, Users } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubAdmin, ClubDetail } from '#lib/clubs.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let { club, canEdit, onchange }: { club: ClubDetail; canEdit: boolean; onchange: () => void } = $props();

	let addOpen = $state(false);
	let removing = $state<ClubAdmin | null>(null);
	let email = $state('');
	let first = $state('');
	let last = $state('');
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let notice = $state('');
	let busy = $state(false);

	async function add(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const r = await platform('POST', `/clubs/${club.id}/admins`, { email: email.trim(), first_name: first.trim(), last_name: last.trim() }, fetch, { 'Idempotency-Key': crypto.randomUUID() });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = r.message ?? '';
			return;
		}
		addOpen = false;
		notice = `${email.trim()} is now an administrator.`;
		email = first = last = '';
		onchange();
	}

	async function remove() {
		if (!removing) return;
		busy = true;
		error = '';
		const r = await platform('DELETE', `/clubs/${club.id}/admins/${removing.id}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'Could not remove the administrator.');
		notice = `${removing.email} is no longer an administrator.`;
		removing = null;
		onchange();
	}
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			<Users size={16} aria-hidden="true" /> Administrators
			<span class="badge badge-ghost ml-1">{club.admins.length}</span>
			{#if canEdit}<button class="btn btn-outline btn-xs ml-auto" onclick={() => ((addOpen = true), (error = ''), (fields = {}))}><UserPlus size={12} aria-hidden="true" /> Add</button>{/if}
		</div>
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
		{#if club.admins.length === 0}
			<Alert kind="warning" title="No administrators">Nobody can manage this club. Add one.</Alert>
		{/if}
		<ul class="divide-y divide-rule">
			{#each club.admins as admin (admin.id)}
				<li class="flex items-center gap-3 py-2.5">
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-1.5 truncate text-sm font-semibold text-ink">
							{admin.first_name} {admin.last_name}
							{#if admin.is_owner}<span class="badge badge-info badge-sm"><Crown size={10} aria-hidden="true" /> owner</span>{/if}
						</div>
						<div class="truncate font-mono text-[11px] text-muted"><a class="hover:underline" href="/users/{admin.id}">{admin.email}</a></div>
					</div>
					{#if canEdit && !admin.is_owner}
						<button class="btn btn-ghost btn-square btn-sm text-club-dark hover:bg-danger-bg" aria-label="Remove {admin.email}" onclick={() => ((removing = admin), (error = ''))}><Trash2 size={14} aria-hidden="true" /></button>
					{/if}
				</li>
			{/each}
		</ul>
	</div>
</div>

<Modal bind:open={addOpen} title="Add administrator">
	<form class="flex flex-col gap-4" onsubmit={add}>
		<p class="text-sm text-muted">Gives an existing account full access to this club. If the email has no account yet, we create one and send a password setup link (then a name is required).</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Email" type="email" required autocomplete="off" bind:value={email} error={fields.email} />
		<div class="grid grid-cols-2 gap-3">
			<Field label="First name" autocomplete="off" bind:value={first} error={fields.first_name} />
			<Field label="Last name" autocomplete="off" bind:value={last} error={fields.last_name} />
		</div>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (addOpen = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !email.trim()}>Add administrator</button>
		</div>
	</form>
</Modal>

<Modal open={!!removing} title="Remove administrator?" onclose={() => (removing = null)}>
	<p class="text-sm"><strong>{removing?.email}</strong> stays a member of the club but loses administrator access.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (removing = null)}>Keep</button>
		<button class="btn btn-error" disabled={busy} onclick={remove}>Remove</button>
	</div>
</Modal>
