<script lang="ts">
	// Optional custom invoice number for the manual opening actions. `value` is null when automatic.
	let { value = $bindable<string | null>(null), nextAutomatic = null, error, disabled = false, extra = '' }: { value?: string | null; nextAutomatic?: string | null; error?: string; disabled?: boolean; extra?: string } = $props();

	const id = $props.id();
	let custom = $state(false);
	let text = $state('');

	$effect(() => {
		value = custom && text.trim() ? text.trim() : null;
	});
</script>

<div class="form-control">
	<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox" bind:checked={custom} {disabled} /> Use a custom invoice number</label>
	{#if custom}
		<input
			id="{id}-n"
			class="input input-bordered mt-2 w-full {error ? 'input-error' : ''}"
			maxlength="32"
			bind:value={text}
			{disabled}
			aria-label="Custom invoice number"
			aria-invalid={error ? 'true' : undefined}
			aria-describedby="{id}-d"
		/>
	{:else if nextAutomatic}
		<span class="label-text-alt mt-1 block pl-8">Automatic number: <span class="font-mono">{nextAutomatic}</span></span>
	{/if}
	<span id="{id}-d" class="mt-1 block text-xs {error ? 'text-club-dark' : 'label-text-alt'} {custom ? '' : 'pl-8'}">
		{#if error}{error}{:else if custom}For numbers that must follow another system. It cannot look like the automatic format and cannot be changed afterwards (to correct a wrong invoice, use Void and reissue).{#if extra} {extra}{/if}{:else if !nextAutomatic}Automatic unless you choose a custom number.{/if}
	</span>
</div>
