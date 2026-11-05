<script lang="ts">
	import { Download, FileText, Paperclip, Trash2, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { formatDateTime, formatSize, type Attachment, type AttachmentList } from '#lib/events/types.ts';

	/** The Files tab: download for everyone; upload (several at once) and delete for attendance managers. */
	let { eventId, initial }: { eventId: string; initial: AttachmentList } = $props();

	// svelte-ignore state_referenced_locally
	let list = $state<AttachmentList>($state.snapshot(initial));
	$effect(() => {
		list = $state.snapshot(initial);
	});

	let errorMsg = $state('');
	let busy = $state(false);
	let input: HTMLInputElement | undefined = $state();

	async function upload() {
		const files = input?.files;
		if (!files?.length) return;
		const form = new FormData();
		for (const f of files) form.append('files', f);
		busy = true;
		errorMsg = '';
		const res = await api<Attachment[]>('POST', `/events/${eventId}/attachments`, { form });
		busy = false;
		if (input) input.value = '';
		if (res.ok && res.data) list.attachments = [...list.attachments, ...res.data];
		else errorMsg = res.fields.__all__ || res.fields.files || res.message || t('common.error.generic');
	}

	let deleteOpen = $state(false);
	let deleting = $state<Attachment | null>(null);
	async function confirmDelete() {
		if (!deleting) return;
		const id = deleting.id;
		busy = true;
		errorMsg = '';
		const res = await api('DELETE', `/events/${eventId}/attachments/${id}`);
		busy = false;
		deleteOpen = false;
		if (res.ok) list.attachments = list.attachments.filter((a) => a.id !== id);
		else errorMsg = res.message || t('common.error.generic');
	}
</script>

<div class="grid gap-4">
	{#if errorMsg}<Alert kind="error" title={t('events.files.failed')}>{errorMsg}</Alert>{/if}
	{#if list.can_manage}
		<div class="flex flex-wrap items-center gap-3">
			<label class="btn btn-primary btn-sm cursor-pointer gap-2 {busy ? 'pointer-events-none opacity-60' : ''}">
				<Upload size={14} aria-hidden="true" />
				{busy ? t('events.files.uploading') : t('events.files.upload')}
				<input bind:this={input} type="file" multiple class="sr-only" onchange={upload} disabled={busy} />
			</label>
			<span class="text-xs text-muted">{t('events.files.limits')}</span>
		</div>
	{/if}
	{#if list.attachments.length}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('events.files.col.name')}</th><th>{t('events.files.col.size')}</th><th>{t('events.files.col.uploaded')}</th><th></th></tr></thead>
				<tbody>
					{#each list.attachments as a (a.id)}
						<tr class="hover:bg-paper">
							<td><a class="link inline-flex items-center gap-2 font-semibold" href={a.download_url} download={a.name} data-sveltekit-reload><FileText size={14} aria-hidden="true" /> {a.name}</a></td>
							<td class="font-mono text-xs">{formatSize(a.size)}</td>
							<td class="text-sm text-muted">{formatDateTime(a.created)}{#if a.uploaded_by}<span class="block text-xs">{a.uploaded_by.name}</span>{/if}</td>
							<td class="text-right whitespace-nowrap">
								<a class="btn btn-outline btn-xs gap-1" href={a.download_url} download={a.name} data-sveltekit-reload aria-label={t('events.files.download_named', { name: a.name })}><Download size={13} /> {t('events.files.download')}</a>
								{#if list.can_manage}<button class="btn btn-outline btn-error btn-xs gap-1" type="button" aria-label={t('events.files.delete_named', { name: a.name })} onclick={() => ((deleting = a), (deleteOpen = true))}><Trash2 size={13} /> {t('common.delete')}</button>{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<div class="card card-body items-center py-10 text-center text-muted"><Paperclip size={28} aria-hidden="true" /><p>{t('events.files.none')}</p></div>
	{/if}
</div>

<Modal bind:open={deleteOpen} title={t('events.files.delete')}>
	<div class="grid gap-3">
		<p>{t('events.files.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={busy} onclick={confirmDelete}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
