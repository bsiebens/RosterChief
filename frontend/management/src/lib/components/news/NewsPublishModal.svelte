<script lang="ts">
	import { enhance } from '$app/forms';
	import { Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { isoToLocalInput } from '#lib/events/form-types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { wallToIso } from '#lib/news.ts';

	/** Publish now or schedule, and choose whether to notify the audience. Posts to the page's `?/workflow` action. */
	let { open = $bindable(false), payload = '', publishedAt = null }: { open: boolean; payload?: string; publishedAt?: string | null } = $props();

	let at = $state('');
	let notify = $state(false);
	let busy = $state(false);
	// Start from the item's own date (a scheduled item being re-scheduled), else empty = right now.
	$effect(() => {
		if (open) at = publishedAt && new Date(publishedAt) > new Date() ? isoToLocalInput(publishedAt) : '';
	});
	// The picked club wall-clock time as an instant.
	const iso = $derived(wallToIso(at));
	const future = $derived(!!iso && new Date(iso) > new Date());
	const past = $derived(!!iso && !future);
</script>

<Modal bind:open title={t('news.publish.title')}>
	<form
		method="POST"
		action="?/workflow"
		class="grid gap-4"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				open = false;
			};
		}}>
		<input type="hidden" name="op" value="publish" />
		<input type="hidden" name="payload" value={payload} />
		<input type="hidden" name="published_at" value={iso} />
		<Field label={t('news.publish.when')} type="datetime-local" bind:value={at} help={t('news.publish.when_help')} />
		{#if past}<Alert kind="info">{t('news.publish.past')}</Alert>{/if}
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" name="notify" bind:checked={notify} />
			<span class="grid gap-0.5">
				<span class="text-sm font-semibold text-ink">{t('news.publish.notify')}</span>
				<span class="text-xs text-muted">{t('news.publish.notify_help')}</span>
			</span>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Upload size={16} /> {future ? t('news.publish.schedule') : t('news.publish.go')}</button>
		</div>
	</form>
</Modal>
