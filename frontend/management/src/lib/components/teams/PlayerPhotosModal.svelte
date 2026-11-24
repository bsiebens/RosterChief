<script lang="ts">
	import { enhance } from '$app/forms';
	import { ImagePlus, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { RosterEntry } from '#lib/teams/server.ts';

	/**
	 * Mass-upload of the player pictures of one team. The files are matched to roster players by file name
	 * ("Jane Doe.jpg", "doe_jane.png"); every match can be changed or skipped before anything is sent. Only the player picture changes.
	 */
	let { open = $bindable(false), roster, act, form }: { open: boolean; roster: RosterEntry[]; act: (action: string) => string; form: any } = $props();

	type Row = { file: File; url: string; memberId: string };
	let rows = $state<Row[]>([]);
	let busy = $state(false);
	const id = $props.id();

	const mine = $derived(form?.teamAction === 'playerPhotos');
	const result = $derived<{ saved: number; rows: { index: number; ok: boolean; error: string | null }[]; names: string[] } | null>(form?.done === 'playerPhotos' ? form.photoResult : null);
	const failed = $derived((result?.rows ?? []).filter((r) => !r.ok));
	const topError = $derived(mine && form?.teamError ? (form.teamError as string) : '');
	const fullName = (p: { first_name: string; last_name: string }) => `${p.first_name} ${p.last_name}`.trim();

	const tokens = (s: string) =>
		s
			.normalize('NFD')
			.replace(/\p{M}/gu, '')
			.toLowerCase()
			.replace(/\.[a-z0-9]+$/, '')
			.split(/[^a-z0-9]+/)
			.filter(Boolean);

	/** The roster player whose first and last name words all appear in the file name (the longest name wins a tie); '' when none or unclear. */
	function match(filename: string): string {
		const have = new Set(tokens(filename));
		const hits = roster
			.map((p) => ({ p, words: [...tokens(p.first_name), ...tokens(p.last_name)] }))
			.filter(({ words }) => words.length > 0 && words.every((w) => have.has(w)))
			.sort((a, b) => b.words.length - a.words.length);
		if (hits.length === 0) return '';
		if (hits.length > 1 && hits[0].words.length === hits[1].words.length) return '';
		return hits[0].p.member_id;
	}

	function clear() {
		for (const r of rows) URL.revokeObjectURL(r.url);
		rows = [];
	}

	function onPick(e: Event & { currentTarget: HTMLInputElement }) {
		const files = [...(e.currentTarget.files ?? [])];
		e.currentTarget.value = '';
		const taken = new Set(rows.map((r) => r.memberId).filter(Boolean));
		for (const file of files) {
			let memberId = match(file.name);
			// A second picture for a player who already has one in the list is left for the user to decide.
			if (memberId && taken.has(memberId)) memberId = '';
			if (memberId) taken.add(memberId);
			rows.push({ file, url: URL.createObjectURL(file), memberId });
		}
	}

	const chosen = $derived(rows.filter((r) => r.memberId));
	const duplicates = $derived(new Set(chosen.map((r) => r.memberId)).size !== chosen.length);
	const unmatched = $derived(rows.length - chosen.length);
</script>

<Modal bind:open title={t('teampage.pp.title')} wide onclose={clear}>
	{#if result}
		<div class="grid gap-3">
			{#if result.saved > 0}<Alert kind="success">{t('teampage.pp.saved', { n: result.saved })}</Alert>{/if}
			{#if failed.length}
				<Alert kind="error" title={t('teampage.pp.failed')}>
					<ul class="list-disc pl-5">
						{#each failed as r (r.index)}<li><span class="font-semibold">{result.names[r.index]}</span>: {r.error}</li>{/each}
					</ul>
				</Alert>
			{/if}
			<div class="modal-action mt-2 flex justify-end">
				<button class="btn btn-primary" type="button" onclick={() => ((open = false), clear())}>{t('teampage.pp.close')}</button>
			</div>
		</div>
	{:else}
		<form
			method="POST"
			action={act('uploadPlayerPhotos')}
			enctype="multipart/form-data"
			class="grid gap-3"
			use:enhance={({ formData }) => {
				// The files travel with the player picked for each of them, in the same order; skipped pictures stay home.
				formData.delete('files');
				formData.delete('member_ids');
				for (const r of chosen) {
					formData.append('files', r.file);
					formData.append('member_ids', r.memberId);
				}
				busy = true;
				return async ({ update }) => {
					await update({ reset: false });
					busy = false;
					clear();
				};
			}}
		>
			<p class="text-sm text-muted">{t('teampage.pp.intro')}</p>
			{#if topError}<Alert kind="error">{topError}</Alert>{/if}
			<div>
				<label class="btn btn-outline gap-2" for="{id}-files"><ImagePlus size={16} aria-hidden="true" /> {t('teampage.pp.choose')}</label>
				<input id="{id}-files" type="file" multiple accept="image/jpeg,image/png,image/webp" class="sr-only" onchange={onPick} />
			</div>
			{#if rows.length}
				<ul class="divide-y divide-rule rounded-lg border border-line">
					{#each rows as r, i (r.url)}
						<li class="flex flex-wrap items-center gap-3 px-3 py-2">
							<img src={r.url} alt="" class="h-12 w-12 shrink-0 rounded-full object-cover" />
							<span class="min-w-0 flex-1 truncate text-sm" title={r.file.name}>{r.file.name}</span>
							<select class="select select-bordered select-sm w-full sm:w-64 {r.memberId ? '' : 'select-warning'}" aria-label={t('teampage.pp.col_player')} bind:value={r.memberId}>
								<option value="">{t('teampage.pp.none')}</option>
								{#each roster as p (p.member_id)}<option value={p.member_id}>{fullName(p)}</option>{/each}
							</select>
							<button class="btn btn-ghost btn-xs" type="button" onclick={() => (URL.revokeObjectURL(r.url), rows.splice(i, 1))}>{t('teampage.pp.skip')}</button>
						</li>
					{/each}
				</ul>
				{#if unmatched}<p class="text-sm text-warning">{t('teampage.pp.unmatched', { n: unmatched })}</p>{/if}
				{#if duplicates}<p class="text-sm text-error">{t('teampage.pp.duplicate')}</p>{/if}
			{/if}
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => ((open = false), clear())}>{t('common.cancel')}</button>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy || chosen.length === 0 || duplicates}>
					<Upload size={16} aria-hidden="true" />
					{chosen.length === 1 ? t('teampage.pp.submit_one') : t('teampage.pp.submit', { n: chosen.length })}
				</button>
			</div>
		</form>
	{/if}
</Modal>
