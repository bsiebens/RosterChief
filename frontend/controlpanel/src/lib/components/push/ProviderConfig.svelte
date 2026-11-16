<script lang="ts">
	import { Pause, Play } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import Modal from '#lib/components/Modal.svelte';
	import type { PushConfig } from '#lib/push.ts';

	let { provider, onToggled }: { provider: PushConfig['providers'][number]; onToggled: () => void } = $props();

	let busy = $state(false);
	let result = $state<{ ok: boolean; detail: string } | null>(null);

	let confirmOpen = $state(false);
	let toggling = $state(false);
	let toggleError = $state('');

	async function toggle(enabled: boolean) {
		toggling = true;
		toggleError = '';
		const r = await platform('PATCH', '/push/config', { [`${provider.provider}_enabled`]: enabled });
		toggling = false;
		confirmOpen = false;
		if (!r.ok) toggleError = r.message ?? 'The change could not be saved.';
		onToggled();
	}

	async function validate() {
		busy = true;
		result = null;
		const r = await platform<{ ok: boolean; detail: string }>('POST', `/push/config/validate/${provider.provider}`);
		busy = false;
		result = r.ok && r.data ? r.data : { ok: false, detail: r.message ?? 'The credentials could not be validated.' };
	}
</script>

<div class="card flex flex-col">
	<div class="card-body flex-1 gap-2 text-sm">
		<div class="card-title">
			{provider.label}
			{#if !provider.enabled}
				<span class="badge badge-ghost ml-auto">disabled</span>
			{:else}
				<span class="badge {provider.configured ? 'badge-success' : 'badge-warning'} ml-auto">{provider.configured ? 'configured' : 'not configured'}</span>
			{/if}
		</div>
		<div class="flex items-center justify-between gap-3">
			<span><strong class="text-ink">{provider.enabled ? 'Enabled' : 'Disabled'}</strong> <span class="text-muted">· {provider.enabled ? 'pushes are sent through this provider' : 'nothing is sent through this provider'}</span></span>
			{#if provider.enabled}
				<button class="btn btn-outline btn-error btn-sm shrink-0" disabled={toggling} onclick={() => (confirmOpen = true)}><Pause size={14} aria-hidden="true" /> Disable</button>
			{:else}
				<button class="btn btn-outline btn-success btn-sm shrink-0" disabled={toggling} onclick={() => toggle(true)}><Play size={14} aria-hidden="true" /> Enable</button>
			{/if}
		</div>
		{#if toggleError}<p class="text-xs text-club-dark">{toggleError}</p>{/if}
		{#if provider.missing.length}
			<div>
				<div class="text-xs text-muted">Missing environment variables</div>
				<ul class="mt-1 flex flex-wrap gap-1.5">{#each provider.missing as name (name)}<li class="rounded border border-edge px-1.5 py-0.5 font-mono text-[11px] text-ink">{name}</li>{/each}</ul>
			</div>
		{:else}
			<p class="text-xs text-muted">All credentials are present in the environment.</p>
		{/if}
		{#if result}
			<div class="rounded border px-3 py-2 text-xs {result.ok ? 'border-ok-border bg-ok-bg text-ok-text' : 'border-danger-border bg-danger-bg text-club-dark'}" role="status">
				<strong>{result.ok ? 'Credentials are valid.' : 'Validation failed.'}</strong>
				{#if result.detail}<div class="mt-0.5 font-mono break-words">{result.detail}</div>{/if}
			</div>
		{/if}
		<div class="mt-auto flex justify-end pt-2"><button class="btn btn-outline btn-sm" disabled={busy || !provider.configured} onclick={validate}>Validate credentials</button></div>
	</div>
</div>

<Modal bind:open={confirmOpen} title="Disable {provider.label}?">
	<p class="text-sm">Nothing is sent through {provider.label} while it is disabled. Devices on this provider stop receiving pushes, and deliveries are skipped without being logged. You can enable it again at any time.</p>
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (confirmOpen = false)}>Cancel</button>
		<button class="btn btn-outline btn-error" disabled={toggling} onclick={() => toggle(false)}><Pause size={14} aria-hidden="true" /> Disable</button>
	</div>
</Modal>
