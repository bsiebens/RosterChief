<script lang="ts">
	import { goto } from '$app/navigation';
	import { Archive, ArchiveRestore, Trash2 } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { ClubDetail } from '#lib/clubs.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { club, isSuperuser, onchange }: { club: ClubDetail; isSuperuser: boolean; onchange: () => void } = $props();

	let archiveOpen = $state(false);
	let deleteOpen = $state(false);
	let confirm = $state('');
	let error = $state('');
	let busy = $state(false);
	const archived = $derived(!!club.archived_at);

	async function toggleArchive() {
		busy = true;
		error = '';
		const r = await platform('POST', `/clubs/${club.id}/${archived ? 'restore' : 'archive'}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'That did not work.');
		archiveOpen = false;
		onchange();
	}

	async function remove() {
		busy = true;
		error = '';
		const r = await platform('DELETE', `/clubs/${club.id}`, { confirm_slug: confirm.trim() });
		busy = false;
		if (!r.ok) return (error = r.fields.confirm_slug ?? r.message ?? 'The club could not be deleted.');
		await goto('/clubs', { refreshAll: true });
	}
</script>

<div class="card border-danger-border">
	<div class="card-body gap-4">
		<div class="card-title text-club-dark"><Trash2 size={16} aria-hidden="true" /> Danger zone</div>

		<div class="flex flex-wrap items-center justify-between gap-3">
			<div class="max-w-xl text-sm">
				<div class="font-semibold text-ink">{archived ? 'Restore this club' : 'Archive this club'}</div>
				<p class="text-muted">{archived ? 'The subdomain resolves again and the club is usable.' : 'The subdomain stops resolving and the club leaves the active lists. All data is kept and the club can be restored.'}</p>
			</div>
			<button class="btn {archived ? 'btn-outline btn-success' : 'btn-warning'}" onclick={() => ((archiveOpen = true), (error = ''))}>
				{#if archived}<ArchiveRestore size={14} aria-hidden="true" /> Restore{:else}<Archive size={14} aria-hidden="true" /> Archive{/if}
			</button>
		</div>

		{#if isSuperuser}
			<div class="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
				<div class="max-w-xl text-sm">
					<div class="font-semibold text-ink">Delete this club</div>
					<p class="text-muted">Permanently removes the club and its entire database schema: members, teams, events, everything. This cannot be undone.</p>
				</div>
				<button class="btn btn-error" onclick={() => ((deleteOpen = true), (confirm = ''), (error = ''))}><Trash2 size={14} aria-hidden="true" /> Delete…</button>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={archiveOpen} title={archived ? 'Restore club?' : 'Archive club?'}>
	<p class="text-sm">{archived ? `“${club.name}” becomes available again.` : `“${club.name}” stops being reachable on its subdomain until it is restored.`}</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (archiveOpen = false)}>Cancel</button>
		<button class="btn {archived ? 'btn-success' : 'btn-warning'}" disabled={busy} onclick={toggleArchive}>{archived ? 'Restore' : 'Archive'}</button>
	</div>
</Modal>

<Modal bind:open={deleteOpen} title="Delete club permanently?">
	<p class="text-sm">This deletes <strong>{club.name}</strong> and everything in it. To confirm, type the slug <code class="font-mono font-bold">{club.slug}</code>.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<input class="input input-bordered mt-3 w-full font-mono" aria-label="Type the club slug to confirm" autocomplete="off" spellcheck="false" bind:value={confirm} />
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (deleteOpen = false)}>Cancel</button>
		<button class="btn btn-error" disabled={busy || confirm.trim() !== club.slug} onclick={remove}>Delete forever</button>
	</div>
</Modal>
