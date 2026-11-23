<script lang="ts">
	import { onMount } from 'svelte';
	import { chatApi, errorText } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/** The chat settings of the club: how long messages are kept (people who may moderate). `onsaved` closes the dialog. */
	let { onsaved }: { onsaved?: () => void } = $props();
	let months = $state<number | null>(null);
	let loaded = $state(false);
	let error = $state('');
	let busy = $state(false);

	onMount(async () => {
		const res = await chatApi.settings();
		if (res.ok && res.data) months = res.data.retention_months;
		else error = errorText(res, t('chat.err.load'));
		loaded = true;
	});

	const problem = $derived(months !== null && (!Number.isInteger(months) || months < 1 || months > 120) ? t('chat.settings.retention_invalid') : '');

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (problem || busy) {
			error = problem;
			return;
		}
		busy = true;
		error = '';
		const res = await chatApi.saveSettings(months);
		busy = false;
		if (res.ok && res.data) {
			months = res.data.retention_months;
			onsaved?.();
		} else error = errorText(res, t('chat.err.generic'));
	}
</script>

<form class="grid gap-4" onsubmit={save}>
	{#if !loaded}
		<p class="text-sm text-muted" role="status">{t('chat.loading')}</p>
	{:else}
		<label class="grid gap-1 text-sm font-semibold text-ink">{t('chat.settings.retention')}
			<input class="input w-full font-normal" type="number" min="1" max="120" step="1" inputmode="numeric" bind:value={months} placeholder={t('chat.settings.forever')} />
			<span class="text-xs font-normal text-muted">{t('chat.settings.retention_help')}</span>
		</label>
		{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
		<div><button class="btn btn-primary" type="submit" disabled={busy}>{t('chat.settings.save')}</button></div>
	{/if}
</form>
