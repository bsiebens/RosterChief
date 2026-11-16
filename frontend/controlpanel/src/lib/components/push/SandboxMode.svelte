<script lang="ts">
	import { FlaskConical, Radio } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import type { SandboxOverview, SandboxState } from '#lib/push.ts';
	import { withZone } from '#lib/time.ts';

	let { sandbox, onchanged }: { sandbox: SandboxState; onchanged: (o: SandboxOverview | null) => void } = $props();

	const EXPIRY: [string, string][] = [
		['30', '30 minutes'],
		['60', '1 hour'],
		['240', '4 hours'],
		['1440', '24 hours'],
		['never', 'Never (until I switch it off)']
	];

	let open = $state(false);
	let expiry = $state('60');
	let busy = $state(false);
	let error = $state('');

	async function set(enabled: boolean) {
		busy = true;
		error = '';
		const r = await platform<SandboxOverview>('PUT', '/push/sandbox/mode', { enabled, confirm: true, expires_in_minutes: enabled && expiry !== 'never' ? Number(expiry) : null });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The mode could not be changed.');
		open = false;
		onchanged(r.data ?? null);
	}
</script>

<div class="card {sandbox.effective ? 'border-2 border-warn' : ''}">
	<div class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
		<div class="min-w-0 flex-1">
			{#if sandbox.effective}
				<div class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><FlaskConical size={16} aria-hidden="true" /> Sandbox: nothing is sent to users</div>
				<p class="mt-0.5 text-xs text-muted">
					Pushes and notification emails are captured below.
					{#if sandbox.expires_at}Switches back to live at {withZone(sandbox.expires_at)}.{:else if sandbox.enabled === null && sandbox.default_from_settings}On because the server is configured that way (PUSH_SANDBOX).{:else}Stays on until you switch back.{/if}
				</p>
			{:else}
				<div class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><Radio size={16} aria-hidden="true" /> Live: pushes are sent for real</div>
				<p class="mt-0.5 text-xs text-muted">Switch to the sandbox to capture pushes and simulate provider failures. Account emails and invoices are never affected.</p>
			{/if}
		</div>
		{#if sandbox.effective}
			<button class="btn btn-outline btn-sm" disabled={busy} onclick={() => set(false)}><Radio size={14} aria-hidden="true" /> Switch back to live</button>
		{:else}
			<button class="btn btn-outline btn-sm" onclick={() => (open = true)}><FlaskConical size={14} aria-hidden="true" /> Switch to sandbox</button>
		{/if}
	</div>
	{#if error && !open}<div class="px-4 pb-3"><Alert kind="error">{error}</Alert></div>{/if}
</div>

<Modal bind:open title="Switch to sandbox mode">
	<div class="flex flex-col gap-4">
		<p class="text-sm">Nobody will receive push notifications until you switch back. Account emails and invoices are not affected.</p>
		<div class="form-control w-full">
			<label class="label" for="sandbox-expiry"><span class="label-text">Switch back to live automatically after</span></label>
			<select id="sandbox-expiry" class="select select-bordered w-full" bind:value={expiry}>
				{#each EXPIRY as [value, label] (value)}<option {value}>{label}</option>{/each}
			</select>
		</div>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)}>Cancel</button>
			<button type="button" class="btn btn-error" disabled={busy} onclick={() => set(true)}>Switch to sandbox</button>
		</div>
	</div>
</Modal>
