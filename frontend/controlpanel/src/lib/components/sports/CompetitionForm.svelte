<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { CatalogFeature } from '#lib/features.ts';
	import type { Competition, Sport } from '#lib/sports.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let {
		open = $bindable(false),
		competition,
		defaultSportId,
		sports,
		features,
		onsaved
	}: { open: boolean; competition: Competition | null; defaultSportId: string; sports: Sport[]; features: CatalogFeature[]; onsaved: (message: string) => void } = $props();

	let name = $state('');
	let module = $state('');
	let sportId = $state('');
	let featureCode = $state('');
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let busy = $state(false);

	const compFeatures = $derived(features.filter((f) => f.code.startsWith('competition_')));
	const otherFeatures = $derived(features.filter((f) => !f.code.startsWith('competition_')));
	const id = $props.id();

	let wasOpen = false;
	$effect(() => {
		if (open && !wasOpen) {
			name = competition?.name ?? '';
			module = competition?.module ?? '';
			sportId = competition ? (competition.sport_id ?? '') : defaultSportId;
			featureCode = competition?.feature_code ?? '';
			error = '';
			fields = {};
			busy = false;
		}
		wasOpen = open;
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const body = { name: name.trim(), module: module.trim(), sport_id: sportId || null, feature_code: featureCode || null };
		const r = competition ? await platform('PATCH', `/competitions/${competition.id}`, body) : await platform('POST', '/competitions', body);
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = Object.keys(r.fields).length ? '' : (r.message ?? 'The competition could not be saved.');
			return;
		}
		open = false;
		onsaved(competition ? `Competition “${body.name}” saved.` : `Competition “${body.name}” added.`);
	}
</script>

<Modal bind:open title={competition ? `Edit competition · ${competition.name}` : 'Add competition'}>
	<form class="flex flex-col gap-4" onsubmit={submit}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Name" bind:value={name} required error={fields.name} autocomplete="off" />
		<Field label="Module" bind:value={module} required error={fields.module} autocomplete="off" help="Dotted Python path of the class that fetches fixtures, e.g. events.competitions.rbihf" />
		<div class="form-control">
			<label class="label" for="{id}-sport"><span class="label-text">Sport</span></label>
			<select id="{id}-sport" class="select select-bordered w-full" bind:value={sportId} aria-invalid={fields.sport_id ? 'true' : undefined}>
				<option value="">No sport</option>
				{#each sports as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
			</select>
			{#if fields.sport_id}<span class="mt-1 block text-xs text-club-dark">{fields.sport_id}</span>{/if}
		</div>
		<div class="form-control">
			<label class="label" for="{id}-feature"><span class="label-text">Required feature</span></label>
			<select id="{id}-feature" class="select select-bordered w-full" bind:value={featureCode} aria-invalid={fields.feature_code ? 'true' : undefined} aria-describedby="{id}-fhelp">
				<option value="">None — every club</option>
				{#if compFeatures.length}
					<optgroup label="Competition features">
						{#each compFeatures as f (f.code)}<option value={f.code}>{f.name}</option>{/each}
					</optgroup>
				{/if}
				{#if otherFeatures.length}
					<optgroup label="Other features">
						{#each otherFeatures as f (f.code)}<option value={f.code}>{f.name}</option>{/each}
					</optgroup>
				{/if}
			</select>
			{#if fields.feature_code}<span class="mt-1 block text-xs text-club-dark">{fields.feature_code}</span>{/if}
			<span id="{id}-fhelp" class="label-text-alt mt-1 block">Clubs can only use the competition when their plan or override includes this feature.</span>
		</div>
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-ghost btn-sm" onclick={() => (open = false)}>Cancel</button>
			<button class="btn btn-primary btn-sm" disabled={busy || !name.trim() || !module.trim()}>{competition ? 'Save' : 'Add competition'}</button>
		</div>
	</form>
</Modal>
