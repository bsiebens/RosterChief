<script lang="ts">
	let {
		label,
		value = $bindable(''),
		error,
		help
	}: { label: string; value?: string; error?: string; help?: string } = $props();

	const id = $props.id();
	const HEX = /^#[0-9a-f]{6}$/;
	// The native colour input cannot be empty: show a neutral grey while "theme default" (empty) is chosen.
	const swatch = $derived(HEX.test(value) ? value : '#d4d8de');

	function onText(e: Event & { currentTarget: HTMLInputElement }) {
		let v = e.currentTarget.value.trim().toLowerCase();
		if (v && !v.startsWith('#')) v = `#${v}`;
		value = v;
	}
</script>

<div class="form-control w-full">
	<label class="label" for="{id}-hex"><span class="label-text">{label}</span></label>
	<div class="flex items-center gap-2">
		<input
			type="color"
			class="h-10 w-12 shrink-0 cursor-pointer rounded-md border border-line bg-white p-1 {HEX.test(value) ? '' : 'opacity-50'}"
			aria-label="{label}: pick a colour"
			value={swatch}
			oninput={(e) => (value = e.currentTarget.value.toLowerCase())}
		/>
		<input
			id="{id}-hex"
			class="input input-bordered w-full font-mono {error ? 'input-error' : ''}"
			placeholder="Theme default"
			maxlength="7"
			spellcheck="false"
			autocomplete="off"
			{value}
			oninput={onText}
			aria-invalid={error ? 'true' : undefined}
			aria-describedby={error ? `${id}-err` : help ? `${id}-help` : undefined}
		/>
		<button type="button" class="btn btn-outline" disabled={!value} onclick={() => (value = '')}>Reset</button>
	</div>
	{#if error}
		<span id="{id}-err" class="mt-1 block text-xs text-club-dark">{error}</span>
	{:else if help}
		<span id="{id}-help" class="label-text-alt mt-1 block">{help}</span>
	{/if}
</div>
