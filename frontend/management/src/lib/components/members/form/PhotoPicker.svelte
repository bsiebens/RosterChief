<script lang="ts">
	import Avatar from '#lib/components/Avatar.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** One picture slot of the member form: current picture, file input with a live preview, and a remove checkbox. */
	let {
		name,
		label,
		help,
		url = null,
		first_name = '',
		last_name = '',
		error
	}: { name: string; label: string; help: string; url?: string | null; first_name?: string; last_name?: string; error?: string } = $props();

	let preview = $state<string | null>(null);
	let remove = $state(false);
	const id = $props.id();

	function onPick(e: Event & { currentTarget: HTMLInputElement }) {
		if (preview) URL.revokeObjectURL(preview);
		const file = e.currentTarget.files?.[0];
		preview = file ? URL.createObjectURL(file) : null;
		if (file) remove = false;
	}
</script>

<div class="flex flex-wrap items-center gap-4">
	<Avatar {first_name} {last_name} photoUrl={preview ?? (remove ? null : url)} size="h-20 w-20" text="text-2xl" />
	<div class="min-w-0 flex-1">
		<label class="label" for={id}><span class="label-text">{label}</span></label>
		<input {id} type="file" {name} accept="image/jpeg,image/png,image/webp" class="file-input file-input-bordered w-full max-w-md {error ? 'input-error' : ''}" onchange={onPick} />
		{#if error}<span class="mt-1 block text-xs text-error">{error}</span>{:else}<span class="label-text-alt mt-1 block">{help}</span>{/if}
		{#if url && !preview}
			<label class="label mt-1 cursor-pointer justify-start gap-2">
				<input type="checkbox" name="remove_{name}" class="checkbox checkbox-sm" bind:checked={remove} />
				<span class="label-text">{t('members.form.photo_remove')}</span>
			</label>
		{/if}
	</div>
</div>
