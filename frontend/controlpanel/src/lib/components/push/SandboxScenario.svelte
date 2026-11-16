<script lang="ts">
	import { untrack } from 'svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import type { SandboxOverview } from '#lib/push.ts';

	let { overview, onchanged }: { overview: SandboxOverview; onchanged: (o: SandboxOverview | null) => void } = $props();

	const id = $props.id();
	const on = $derived(overview.state.effective);
	let scenario = $state(untrack(() => overview.state.scenario));
	let latency = $state(String(untrack(() => overview.state.latency_ms)));
	let busy = $state(false);
	let error = $state('');
	let saved = $state(false);

	$effect(() => {
		scenario = overview.state.scenario;
		latency = String(overview.state.latency_ms);
	});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		saved = false;
		const n = Number(latency);
		if (!/^\d+$/.test(latency.trim()) || n > 10000) return (error = 'Latency must be a whole number of milliseconds between 0 and 10000.');
		busy = true;
		const r = await platform<SandboxOverview>('PUT', '/push/sandbox/scenario', { scenario, latency_ms: n });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The scenario could not be saved.');
		saved = true;
		onchanged(r.data ?? null);
	}
</script>

<div class="card">
	<form class="card-body gap-3 text-sm" onsubmit={submit} novalidate>
		<div class="card-title">Provider scenario</div>
		{#if !on}<p class="text-muted">The scenario only applies while the sandbox is on. Switch to sandbox mode to change it.</p>{/if}
		<fieldset class="grid gap-3 sm:grid-cols-[1fr_10rem]" disabled={!on || busy}>
			<div class="form-control w-full">
				<label class="label" for="{id}-scenario"><span class="label-text">What the fake provider answers</span></label>
				<select id="{id}-scenario" class="select select-bordered w-full" bind:value={scenario}>
					{#each overview.scenarios as s (s.value)}<option value={s.value}>{s.label}</option>{/each}
				</select>
			</div>
			<div class="form-control w-full">
				<label class="label" for="{id}-latency"><span class="label-text">Latency (ms)</span></label>
				<input id="{id}-latency" class="input input-bordered w-full" type="number" min="0" max="10000" step="50" bind:value={latency} />
			</div>
		</fieldset>
		<ul class="space-y-1 text-xs text-muted">
			{#each overview.scenarios as s (s.value)}<li><strong class="text-ink">{s.label}</strong>: {s.description}</li>{/each}
		</ul>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if saved}<Alert kind="success">Scenario saved.</Alert>{/if}
		<div class="flex justify-end"><button class="btn btn-outline btn-sm" type="submit" disabled={!on || busy}>Set scenario</button></div>
	</form>
</div>
