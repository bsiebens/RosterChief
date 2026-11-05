<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLInputAttributes } from 'svelte/elements';

	/** A labelled input. `aside` renders on the help / error line, right-aligned (e.g. quick-set buttons). */
	let {
		label,
		value = $bindable(''),
		error,
		help,
		aside,
		...rest
	}: { label: string; value?: string; error?: string; help?: string; aside?: Snippet } & Omit<HTMLInputAttributes, 'value'> = $props();

	const id = $props.id();
</script>

{#snippet note()}
	{#if error}
		<span id="{id}-err" class="mt-1 block text-xs text-error">{error}</span>
	{:else if help}
		<span id="{id}-help" class="label-text-alt mt-1 block">{help}</span>
	{/if}
{/snippet}

<div class="form-control w-full">
	<label class="label" for={id}><span class="label-text">{label}</span></label>
	<input
		{id}
		class="input input-bordered w-full {error ? 'input-error' : ''}"
		bind:value
		aria-invalid={error ? 'true' : undefined}
		aria-describedby={error ? `${id}-err` : help ? `${id}-help` : undefined}
		{...rest}
	/>
	{#if aside}
		<div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
			<div class="min-w-0 flex-1 basis-32">{@render note()}</div>
			<div class="mt-1 ml-auto">{@render aside()}</div>
		</div>
	{:else}
		{@render note()}
	{/if}
</div>
