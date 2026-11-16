<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { Sport } from '#lib/sports.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let {
		mode = $bindable<'add' | 'rename' | 'delete' | null>(null),
		sport,
		ondone
	}: { mode: 'add' | 'rename' | 'delete' | null; sport: Sport | null; ondone: (message: string, createdId?: string, deleted?: boolean) => void } = $props();

	let name = $state('');
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let busy = $state(false);

	let open = $state(false);
	let was: typeof mode = null;
	$effect(() => {
		if (mode !== was) {
			if (mode) {
				name = mode === 'rename' ? (sport?.name ?? '') : '';
				error = '';
				fields = {};
				busy = false;
			}
			open = mode !== null;
			was = mode;
		}
	});
	$effect(() => {
		if (!open && mode !== null) {
			was = null;
			mode = null;
		}
	});

	async function save(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const r = mode === 'add' ? await platform<Sport>('POST', '/sports', { name: name.trim() }) : await platform<Sport>('PATCH', `/sports/${sport!.id}`, { name: name.trim() });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = r.message ?? 'The sport could not be saved.';
			return;
		}
		const m = mode;
		mode = null;
		ondone(m === 'add' ? `Sport “${name.trim()}” added.` : `Sport renamed to “${name.trim()}”.`, m === 'add' ? r.data?.id : undefined);
	}

	async function remove() {
		busy = true;
		error = '';
		const r = await platform('DELETE', `/sports/${sport!.id}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The sport could not be deleted.');
		const n = sport!.name;
		mode = null;
		ondone(`Sport “${n}” deleted.`, undefined, true);
	}
</script>

<Modal bind:open title={mode === 'add' ? 'Add sport' : mode === 'rename' ? 'Rename sport' : 'Delete sport'}>
	{#if mode === 'delete' && sport}
		<div class="flex flex-col gap-4">
			<p class="text-sm">Delete <strong>{sport.name}</strong>? Its referee types are deleted with it. This only works for a sport no club or competition uses.</p>
			{#if error}<Alert kind="error">{error}</Alert>{/if}
			<div class="flex justify-end gap-2">
				<button class="btn btn-ghost btn-sm" onclick={() => (open = false)}>Cancel</button>
				<button class="btn btn-error btn-sm" disabled={busy} onclick={remove}>Delete sport</button>
			</div>
		</div>
	{:else if mode}
		<form class="flex flex-col gap-4" onsubmit={save}>
			{#if error && !fields.name}<Alert kind="error">{error}</Alert>{/if}
			<Field label="Name" bind:value={name} required error={fields.name} autocomplete="off" help={mode === 'rename' ? 'The slug does not change when a sport is renamed.' : 'The slug is generated from the name and never changes.'} />
			<div class="flex justify-end gap-2">
				<button type="button" class="btn btn-ghost btn-sm" onclick={() => (open = false)}>Cancel</button>
				<button class="btn btn-primary btn-sm" disabled={busy || !name.trim()}>{mode === 'add' ? 'Add sport' : 'Save'}</button>
			</div>
		</form>
	{/if}
</Modal>
