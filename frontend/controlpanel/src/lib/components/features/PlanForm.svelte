<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { CatalogFeature, FullPlan } from '#lib/features.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let { open = $bindable(false), plan, catalog, onsaved }: { open: boolean; plan: FullPlan | null; catalog: CatalogFeature[]; onsaved: (message: string) => void } = $props();

	let code = $state('');
	let name = $state('');
	let description = $state('');
	let active = $state(true);
	let picked = $state<Record<string, boolean>>({});
	let limits = $state<Record<string, string>>({});
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let busy = $state(false);

	// Reset the form each time the dialog opens (not on every prop change).
	let wasOpen = false;
	$effect(() => {
		if (open && !wasOpen) {
			code = plan?.code ?? '';
			name = plan?.name ?? '';
			description = plan?.description ?? '';
			active = plan?.is_active ?? true;
			picked = Object.fromEntries((plan?.features ?? []).map((f) => [f.code, true]));
			limits = Object.fromEntries((plan?.features ?? []).filter((f) => f.limit != null).map((f) => [f.code, String(f.limit)]));
			error = '';
			fields = {};
		}
		wasOpen = open;
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const features = catalog.filter((f) => picked[f.code]).map((f) => ({ code: f.code, limit: f.kind === 'limit' && (limits[f.code] ?? '') !== '' ? Number(limits[f.code]) : null }));
		const r = plan
			? await platform('PATCH', `/plans/${plan.id}`, { name: name.trim(), description: description.trim(), is_active: active, features })
			: await platform('POST', '/plans', { code: code.trim(), name: name.trim(), description: description.trim(), features });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error = r.message ?? 'The plan could not be saved.';
			return;
		}
		open = false;
		onsaved(plan ? `Plan “${name.trim()}” saved.` : `Plan “${name.trim()}” created.`);
	}
</script>

<Modal bind:open title={plan ? `Edit plan · ${plan.name}` : 'New plan'} wide>
	<form class="flex flex-col gap-4" onsubmit={submit}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="grid gap-4 sm:grid-cols-2">
			{#if plan}
				<Field label="Code" value={plan.code} readonly help="The code cannot be changed." />
			{:else}
				<Field label="Code" bind:value={code} required error={fields.code} help="Short and permanent, e.g. “club-plus”." autocomplete="off" />
			{/if}
			<Field label="Name" bind:value={name} required error={fields.name} autocomplete="off" />
		</div>
		<div class="form-control">
			<label class="label" for="plan-desc"><span class="label-text">Description</span></label>
			<textarea id="plan-desc" name="description" class="textarea textarea-bordered w-full" rows="2" bind:value={description}></textarea>
			{#if fields.description}<span class="mt-1 block text-xs text-club-dark">{fields.description}</span>{/if}
		</div>
		{#if plan}
			<label class="flex items-center gap-3 text-sm">
				<input type="checkbox" class="toggle" bind:checked={active} />
				<span>Active <span class="text-muted">(inactive plans cannot be given to clubs; clubs already on it keep it)</span></span>
			</label>
		{/if}

		<fieldset class="flex flex-col gap-2">
			<legend class="label-text mb-1">Features in this plan</legend>
			{#if fields.features}<Alert kind="error">{fields.features}</Alert>{/if}
			<div class="overflow-x-auto rounded border border-line">
				<table class="table">
					<tbody>
						{#each catalog as f (f.code)}
							<tr>
								<td class="w-10"><input id="pf-{f.code}" type="checkbox" class="checkbox" bind:checked={picked[f.code]} aria-label="Include {f.name}" /></td>
								<td>
									<label for="pf-{f.code}" class="block cursor-pointer font-semibold text-ink">{f.name}</label>
									<span class="font-mono text-[11px] text-muted">{f.code}{f.limit_scope ? ` · one ${f.limit_scope} limit per plan` : ''}</span>
								</td>
								<td class="w-48">
									{#if f.kind === 'limit' && picked[f.code]}
										<input class="input input-bordered w-full" type="number" min="0" placeholder="unlimited" aria-label="Limit for {f.name}, empty means unlimited" bind:value={limits[f.code]} />
									{:else if f.kind === 'limit'}<span class="text-xs text-muted">has a limit</span>{/if}
								</td>
							</tr>
						{:else}
							<tr><td class="text-sm text-muted">The feature catalog is empty.</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="text-xs text-muted">Leave a limit empty for unlimited. {plan ? 'Saving replaces the plan’s whole feature set.' : ''}</p>
		</fieldset>

		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-ghost btn-sm" onclick={() => (open = false)}>Cancel</button>
			<button class="btn btn-primary btn-sm" disabled={busy}>{plan ? 'Save plan' : 'Create plan'}</button>
		</div>
	</form>
</Modal>
