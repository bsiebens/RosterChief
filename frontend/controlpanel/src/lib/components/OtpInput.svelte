<script lang="ts">
	// A TOTP code is 6 characters, a recovery code 8 -- the same field takes either. The boxed layout only
	// fits six, so longer input falls back to a plain centred input (same behaviour as the old app).
	let {
		value = $bindable(''),
		boxes = 6,
		label = 'Code',
		error,
		autofocus = false
	}: { value?: string; boxes?: number; label?: string; error?: string; autofocus?: boolean } = $props();

	const id = $props.id();
	const boxed = $derived(value.length <= boxes);
</script>

<div class="form-control w-full items-center">
	<label class="label self-start" for={id}><span class="label-text">{label}</span></label>
	<div class={boxed ? 'otp otp-lg' : 'w-full'}>
		{#if boxed}
			{#each { length: boxes } as _, i (i)}<span aria-hidden="true">{value[i] ?? ''}</span>{/each}
		{/if}
		<!-- svelte-ignore a11y_autofocus -->
		<input
			{id}
			class={boxed ? '' : 'input input-bordered input-lg w-full'}
			bind:value
			inputmode="text"
			autocomplete="one-time-code"
			autocapitalize="off"
			spellcheck="false"
			maxlength="32"
			{autofocus}
			aria-invalid={error ? 'true' : undefined}
			aria-describedby={error ? `${id}-err` : undefined}
		/>
	</div>
	{#if error}<span id="{id}-err" class="mt-2 self-start text-xs text-club-dark">{error}</span>{/if}
</div>
