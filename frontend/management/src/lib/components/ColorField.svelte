<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';

	let {
		label,
		value = $bindable(''),
		error,
		help,
		name,
		placeholder = t('settings.color.default')
	}: { label: string; value?: string; error?: string; help?: string; name?: string; placeholder?: string } = $props();

	const id = $props.id();
	const HEX = /^#[0-9a-f]{6}$/;
	const valid = $derived(HEX.test(value));
	// The native colour input cannot be empty: it only opens the picker, the swatch itself shows the real state.
	const swatch = $derived(valid ? value : '#d4d8de');

	function onText(e: Event & { currentTarget: HTMLInputElement }) {
		let v = e.currentTarget.value.trim().toLowerCase();
		if (v && !v.startsWith('#')) v = `#${v}`;
		value = v;
	}
</script>

<div class="form-control w-full">
	<label class="label" for="{id}-hex"><span class="label-text font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{label}</span></label>
	<div class="relative">
		<label class="absolute top-1/2 left-3 h-5 w-5 -translate-y-1/2 cursor-pointer overflow-hidden rounded-sm border border-line {valid ? '' : 'opacity-60'}" style="background:{swatch}">
			<span class="sr-only">{t('settings.color.pick', { label })}</span>
			<input type="color" class="absolute inset-0 h-full w-full cursor-pointer opacity-0" value={swatch} oninput={(e) => (value = e.currentTarget.value.toLowerCase())} />
		</label>
		<input
			id="{id}-hex"
			class="input input-bordered w-full pr-16 pl-11 font-mono {error ? 'input-error' : ''}"
			{placeholder}
			maxlength="7"
			spellcheck="false"
			autocomplete="off"
			{value}
			oninput={onText}
			aria-invalid={error ? 'true' : undefined}
			aria-describedby={error ? `${id}-err` : help ? `${id}-help` : undefined}
		/>
		{#if name}<input type="hidden" {name} {value} />{/if}
		{#if value}
			<button type="button" class="btn btn-ghost btn-xs absolute top-1/2 right-2 -translate-y-1/2" onclick={() => (value = '')}>{t('settings.color.reset')}</button>
		{/if}
	</div>
	{#if error}
		<span id="{id}-err" class="mt-1 block text-xs text-error">{error}</span>
	{:else if help}
		<span id="{id}-help" class="label-text-alt mt-1 block">{help}</span>
	{/if}
</div>
