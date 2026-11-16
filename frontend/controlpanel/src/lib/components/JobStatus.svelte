<script lang="ts">
	import { onMount } from 'svelte';
	import type { JobsStatus } from '#lib/dashboard.ts';

	let { initial }: { initial: JobsStatus | null } = $props();

	let live = $state<JobsStatus | null>(null);
	let unreachable = $state(false);
	const status = $derived(live ?? initial);

	async function poll() {
		try {
			const res = await fetch('/api/platform/v1/jobs/status');
			if (res.ok) {
				live = await res.json();
				unreachable = false;
			} else unreachable = true;
		} catch {
			unreachable = true;
		}
	}

	onMount(() => {
		if (!initial) poll(); // the server-side lookup failed: don't leave the bar empty until the next tick
		const timer = setInterval(() => !document.hidden && poll(), 30_000);
		return () => clearInterval(timer);
	});

	const tone = $derived(status?.state === 'failing' ? 'warn' : status?.state === 'busy' ? 'ice' : status ? 'ok' : 'dim');
</script>

<a
	class="flex shrink-0 items-center gap-2 font-mono text-[11px] hover:underline {tone === 'warn' ? 'text-warn' : tone === 'ice' ? 'text-ice' : tone === 'ok' ? 'text-ok' : 'text-on-dark-dim'}"
	href="/jobs"
	title="Background jobs"
	data-testid="job-status"
>
	<span class="h-[7px] w-[7px] rounded-full {tone === 'warn' ? 'bg-warn' : tone === 'ice' ? 'animate-pulse bg-ice' : tone === 'ok' ? 'bg-ok' : 'bg-on-dark-faint'}"></span>
	{#if !status}
		{unreachable ? 'jobs status unavailable' : 'checking jobs…'}
	{:else if status.state === 'failing'}
		{status.failed} job failure{status.failed === 1 ? '' : 's'} in the last {status.window_hours}h
	{:else if status.state === 'busy'}
		{status.running} job{status.running === 1 ? '' : 's'} running
	{:else}
		all jobs ok
	{/if}
</a>
