<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, EyeOff, Lock, Send } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { answerText } from '#lib/forms.ts';
	import type { ReleaseRow } from '#lib/evaluations.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	usePageHeader(() => ({ title: t('eval.release.page'), context: data.preview.checklist.name }));

	type Choice = { release: '' | 'notes' | 'full'; note: string };
	// One editable choice per evaluation. Private ones start at the level picked on the meeting page; ones already shared keep theirs.
	const start = (): Record<string, Choice> => Object.fromEntries(data.preview.results.map((row) => [row.id, { release: row.release || data.level, note: row.public_note }])) as Record<string, Choice>;
	// svelte-ignore state_referenced_locally
	let choices = $state<Record<string, Choice>>(start());
	let busy = $state(false);
	const shown = (answer: ReleaseRow['answers'][number]) => answerText({ field_type: answer.field_type, options: answer.options } as never, answer.value, t('common.yes'), t('common.no'));
	const sharedCount = $derived(Object.values(choices).filter((choice) => choice.release).length);
	const items = $derived(JSON.stringify(Object.entries(choices).map(([evaluation_id, choice]) => ({ evaluation_id, release: choice.release, public_note: choice.note }))));
	const setAll = (level: Choice['release']) => {
		for (const choice of Object.values(choices)) choice.release = level;
	};
	const stamp = (value: string) => new Date(value).toLocaleDateString();
</script>

<svelte:head><title>{pageTitle(t('eval.release.page'))}</title></svelte:head>

<div class="grid gap-4">
	{#if form?.message}<Alert kind="error">{form.message}</Alert>{/if}
	<section class="card card-body gap-3">
		<p class="text-sm text-muted">{t('eval.release.preview_help', { season: data.preview.season?.name ?? '' })}</p>
		<div class="flex flex-wrap items-center gap-2">
			<span class="text-sm font-semibold text-ink">{t('eval.release.set_all')}</span>
			<button type="button" class="btn btn-outline btn-xs" onclick={() => setAll('')}>{t('eval.share.none')}</button>
			<button type="button" class="btn btn-outline btn-xs" onclick={() => setAll('notes')}>{t('eval.share.notes')}</button>
			<button type="button" class="btn btn-outline btn-xs" onclick={() => setAll('full')}>{t('eval.share.full')}</button>
		</div>
	</section>

	{#each data.preview.results as row (row.id)}
		{@const choice = choices[row.id]}
		<section class="card card-body gap-3" aria-label={row.player.name}>
			<div>
				<h2 class="font-display text-lg font-extrabold text-ink uppercase">{row.player.name}</h2>
				<p class="text-xs text-muted">{stamp(row.submitted_at)}{row.evaluator ? ` · ${t('profile.eval.by', { name: row.evaluator.name })}` : ''}</p>
			</div>
			<div class="grid gap-1.5">
				<p class="text-sm font-semibold text-ink" id="lvl-{row.id}">{t('eval.share.level')}</p>
				<div class="grid grid-cols-1 gap-2 sm:grid-cols-3" role="radiogroup" aria-labelledby="lvl-{row.id}">
					{#each [['', 'eval.share.none'], ['notes', 'eval.share.notes'], ['full', 'eval.share.full']] as [value, label] (value)}
						<button type="button" role="radio" aria-checked={choice.release === value} class="btn btn-sm h-10 min-h-0 {choice.release === value ? 'btn-primary' : 'btn-outline'}" onclick={() => (choice.release = value as Choice['release'])}>{t(label)}</button>
					{/each}
				</div>
			</div>
			<div class="grid items-start gap-4 lg:grid-cols-2">
				<label class="grid content-start gap-1 text-sm font-semibold">{t('eval.public_note')}
					<textarea class="textarea textarea-bordered w-full font-normal" rows="4" maxlength="2000" bind:value={choice.note}></textarea>
				</label>
				<div class="rounded-box border border-line bg-subhead p-4">
					<p class="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">{t('eval.release.family_sees')}</p>
					{#if !choice.release}
						<p class="flex items-center gap-2 text-sm text-muted"><Lock size={14} aria-hidden="true" />{t('eval.release.stays_private')}</p>
					{:else}
						{#if choice.note.trim()}<p class="text-sm whitespace-pre-line text-ink">{choice.note}</p>{:else}<p class="text-sm text-muted">{t('eval.release.no_note')}</p>{/if}
						{#if choice.release === 'full'}
							<dl class="mt-3 divide-y divide-rule rounded-box border border-line">
								{#each row.answers.filter((answer) => answer.visible && shown(answer)) as answer, i (i)}
									<div class="flex items-center justify-between gap-3 px-3 py-2 text-sm"><dt class="text-muted">{answer.label}</dt><dd class="font-mono font-semibold text-ink">{shown(answer)}</dd></div>
								{/each}
							</dl>
							{#if row.answers.some((answer) => !answer.visible)}
								<p class="mt-2 flex items-center gap-1.5 text-xs text-muted"><EyeOff size={13} aria-hidden="true" />{t('eval.release.hidden', { list: row.answers.filter((answer) => !answer.visible).map((answer) => answer.label).join(', ') })}</p>
							{/if}
						{/if}
					{/if}
				</div>
			</div>
		</section>
	{:else}
		<div class="card card-body items-center py-10 text-center text-muted"><p>{t('eval.release.nothing')}</p></div>
	{/each}

	<form class="sticky bottom-3 flex flex-wrap items-center justify-between gap-3 rounded-box border border-line bg-white p-3 shadow" method="POST" use:enhance={() => { busy = true; return async ({ update }) => { await update(); busy = false; }; }}>
		<input type="hidden" name="items" value={items} />
		<a class="btn btn-outline btn-sm gap-1" href="/evaluations/{data.checklistId}/meeting"><ArrowLeft size={14} /> {t('common.cancel')}</a>
		<span class="text-sm text-muted">{t('eval.release.summary', { shared: sharedCount, total: data.preview.results.length })}</span>
		<button class="btn btn-primary btn-sm gap-1" type="submit" disabled={busy || !data.preview.results.length}><Send size={14} /> {t('eval.release.confirm')}</button>
	</form>
</div>
