<script lang="ts">
	import { FileText, Save, Trash2, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { api } from '#lib/management-api.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const rules = $derived(data.rules);
	const run = new Runner();
	// svelte-ignore state_referenced_locally
	let markdown = $state(data.rules?.markdown ?? '');
	$effect(() => {
		markdown = data.rules?.markdown ?? '';
	});
	const dirty = $derived(markdown !== (rules?.markdown ?? ''));
	const saveText = () => run.put(`/tournaments/${tn.id}/rules`, { markdown }, undefined, { notice: t('tn.rules.saved') });
	let file = $state<HTMLInputElement | undefined>();
	async function upload() {
		const picked = file?.files?.[0];
		if (!picked) return;
		await run.run(
			() => {
				const form = new FormData();
				form.set('document', picked);
				return api('POST', `/tournaments/${tn.id}/rules/file`, { form });
			},
			undefined,
			{ notice: t('tn.rules.uploaded') }
		);
		if (file) file.value = '';
	}
	const removeFile = () => run.del(`/tournaments/${tn.id}/rules/file`, undefined, { notice: t('tn.rules.removed') });

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.rules') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.rules'))}</title></svelte:head>

<div class="grid items-start gap-5 xl:grid-cols-3">
	{#if data.error || !rules}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		<section class="card card-body card-hl gap-3 xl:col-span-2">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><FileText size={16} aria-hidden="true" /> {t('tn.rules.text')}</h2>
			<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
			{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
			<p class="text-xs text-muted">{t('tn.rules.text_help')}</p>
			<textarea class="textarea textarea-bordered min-h-96 w-full font-mono text-sm" aria-label={t('tn.rules.text')} bind:value={markdown}></textarea>
			<div class="flex justify-end"><button class="btn btn-primary gap-2" type="button" disabled={run.busy || !dirty} onclick={saveText}><Save size={16} /> {t('common.save')}</button></div>
		</section>
		<section class="card card-body card-hl gap-3">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Upload size={16} aria-hidden="true" /> {t('tn.rules.document')}</h2>
			<p class="text-xs text-muted">{t('tn.rules.document_help')}</p>
			{#if rules.file_url}
				<div class="flex items-center gap-2 rounded-lg border border-line p-3">
					<FileText size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<a class="link min-w-0 flex-1 truncate text-sm font-semibold" href={rules.file_url} target="_blank" rel="noopener">{rules.file_name}</a>
					<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.rules.remove')} title={t('tn.rules.remove')} disabled={run.busy} onclick={removeFile}><Trash2 size={13} /></button>
				</div>
			{/if}
			<input bind:this={file} type="file" class="file-input file-input-bordered w-full" accept=".pdf,.doc,.docx,.odt" onchange={upload} disabled={run.busy} aria-label={t('tn.rules.document')} />
		</section>
	{/if}
</div>
