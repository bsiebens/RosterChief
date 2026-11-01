<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	let {
		label,
		value = $bindable(''),
		error,
		help,
		mono = false,
		...rest
	}: { label: string; value?: string; error?: string; help?: string; mono?: boolean } & Omit<HTMLInputAttributes, 'value'> = $props();

	const id = $props.id();
</script>

<div class="form-control w-full">
	<label class="label" for={id}><span class="label-text">{label}</span></label>
	<input
		{id}
		class="input input-bordered w-full {mono ? 'font-mono' : ''} {error ? 'input-error' : ''}"
		bind:value
		aria-invalid={error ? 'true' : undefined}
		aria-describedby={error ? `${id}-err` : help ? `${id}-help` : undefined}
		{...rest}
	/>
	{#if error}
		<span id="{id}-err" class="mt-1 block text-xs text-club-dark">{error}</span>
	{:else if help}
		<span id="{id}-help" class="label-text-alt mt-1 block">{help}</span>
	{/if}
</div>
