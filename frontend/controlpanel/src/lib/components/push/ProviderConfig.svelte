<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { PushConfig } from '#lib/push.ts';

	let { provider, onToggled }: { provider: PushConfig['providers'][number]; onToggled: () => void } = $props();

	let busy = $state(false);
	let result = $state<{ ok: boolean; detail: string } | null>(null);

	let toggling = $state(false);
	let toggleError = $state('');

	async function toggle(enabled: boolean) {
		toggling = true;
		toggleError = '';
		const r = await platform('PATCH', '/push/config', { [`${provider.provider}_enabled`]: enabled });
		toggling = false;
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
			<span class="badge {provider.configured ? 'badge-success' : 'badge-warning'} ml-auto">{provider.configured ? 'configured' : 'not configured'}</span>
		</div>
		<label class="flex items-center gap-3">
			<input type="checkbox" class="toggle toggle-sm" checked={provider.enabled} disabled={toggling} onchange={(e) => toggle(e.currentTarget.checked)} />
			<span><strong class="text-ink">{provider.enabled ? 'Enabled' : 'Disabled'}</strong> <span class="text-muted">· when disabled, nothing is sent through this provider</span></span>
		</label>
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
