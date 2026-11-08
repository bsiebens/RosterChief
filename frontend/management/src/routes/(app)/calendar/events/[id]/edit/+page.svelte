<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import EventForm from '#lib/components/events/EventForm.svelte';
	import EventFormScope from '#lib/components/events/EventFormScope.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { eventHref, type EditScope } from '#lib/events/types.ts';

	let { data, form } = $props();
	const back = $derived(eventHref(data.id));
	let deleteOpen = $state(false);
	let deleteScope = $state<EditScope>('this');
	let busy = $state(false);
	const deleteError = $derived(form && 'deleteError' in form ? (form.deleteStatus === 403 ? t('eventform.err.forbidden') : String(form.deleteError)) : '');
	const saveForm = $derived(form && 'fields' in form ? form : null);

	usePageHeader(() => ({ title: t('eventform.title_edit'), context: data.event?.title ?? '', actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href={back}><ArrowLeft size={16} /> {t('eventform.back_event')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eventform.title_edit'))}</title></svelte:head>

{#if deleteError}<div class="mb-4"><Alert kind="error" title={t('eventform.delete_failed')}>{deleteError}</Alert></div>{/if}

{#if data.event}
<EventForm
	mode="edit"
	options={data.options}
	initial={data.initial}
	series={data.series}
	fields={saveForm?.fields}
	message={saveForm?.message}
	status={saveForm?.status}
	cancelHref={back}
	ondelete={() => ((deleteScope = 'this'), (deleteOpen = true))} />
{/if}

<Modal bind:open={deleteOpen} title={t('eventform.delete_title')}>
	<form
		method="POST"
		action="?/delete"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type !== 'redirect') deleteOpen = false;
			};
		}}>
		<input type="hidden" name="scope" value={data.series ? deleteScope : 'this'} />
		<p>{t('eventform.delete_body', { title: data.event?.title ?? '' })}</p>
		{#if data.series}<EventFormScope bind:scope={deleteScope} purpose="delete" />{/if}
		<Alert kind="info">{t('eventform.delete_no_notice')}</Alert>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
