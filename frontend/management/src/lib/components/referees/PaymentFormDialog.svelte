<script lang="ts">
	import { Download, ExternalLink } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { withPageMargins } from '#lib/pdf-preview.ts';
	import { gameLabel, REF_KINDS, type Game, type RefKind } from '#lib/referees.ts';

	/** The payment form of a game as an HTML preview (same sheet as the PDF), with the PDF download. */
	let { open = $bindable(false), game, kind = $bindable<RefKind>('referee'), canSettings = false }: { open: boolean; game: Game | null; kind: RefKind; canSettings?: boolean } = $props();

	let lang = $state<string>('en');
	let html = $state<string | null>(null);
	let failed = $state(false);

	$effect(() => {
		if (open) lang = getLang();
	});

	// Re-render when the game, the kind or the language changes, or after a fee was saved (the game's totals change).
	const key = $derived(game ? `${game.id}|${kind}|${lang}|${game.total}|${game.referees.length}|${game.officials.length}` : '');
	$effect(() => {
		if (!open || !game) return;
		void key;
		const id = game.id;
		const k = kind;
		const l = lang;
		html = null;
		failed = false;
		const url = `/api/v1/referee-management/games/${id}/payment-form?kind=${k}&lang=${l}`;
		fetch(url, { headers: { accept: 'text/html' } })
			.then(async (res) => {
				if (!open || game?.id !== id) return;
				if (res.ok) html = await res.text();
				else failed = true;
			})
			.catch(() => (failed = true));
	});

	const pdfUrl = $derived(game ? `/api/v1/referee-management/games/${game.id}/payment-form.pdf?kind=${kind}&lang=${lang}` : '#');
</script>

<Modal bind:open title={t('referees.form.title')} wide>
	{#if game}
		<div class="grid gap-3">
			<p class="text-sm text-muted">{gameLabel(game)}. {t('referees.form.hint')}</p>
			<div class="flex flex-wrap items-center gap-3">
				<label class="flex items-center gap-2 text-sm"><span class="text-muted">{t('referees.form.kind')}</span>
					<select class="select select-sm w-auto" bind:value={kind}>
						{#each REF_KINDS as k (k)}<option value={k}>{t(`referees.kind.${k}`)}</option>{/each}
					</select></label>
				<label class="flex items-center gap-2 text-sm"><span class="text-muted">{t('referees.form.lang')}</span>
					<select class="select select-sm w-auto" bind:value={lang}>
						<option value="en">English</option><option value="nl">Nederlands</option><option value="fr">Français</option>
					</select></label>
				<div class="flex-1"></div>
				{#if canSettings}<a class="btn btn-outline btn-sm gap-1" href="/settings?tab=pdf"><ExternalLink size={13} aria-hidden="true" /> {t('referees.form.settings')}</a>{/if}
				<a class="btn btn-primary btn-sm gap-1" href={pdfUrl} download><Download size={13} aria-hidden="true" /> {t('referees.form.download')}</a>
			</div>
			{#if failed}
				<Alert kind="error">{t('referees.form.failed')}</Alert>
			{:else if html === null}
				<p class="text-sm text-muted">{t('referees.form.loading')}</p>
			{:else}
				<iframe title={t('referees.form.frame')} sandbox="" srcdoc={withPageMargins(html)} class="h-[calc(100vh-29rem)] min-h-48 w-full rounded-lg border border-line bg-white"></iframe>
			{/if}
		</div>
	{/if}
	<div class="modal-action mt-4"><button type="button" class="btn btn-outline" onclick={() => (open = false)}>{t('common.close')}</button></div>
</Modal>
