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
	<section class="card overflow-hidden">
	<header class="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
		<div>
			<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Paperclip size={16} aria-hidden="true" /> {t('events.tab.files')} <span class="font-mono text-xs font-normal text-muted">· {list.attachments.length}</span></h2>
			{#if list.can_manage}<p class="text-xs text-muted">{t('events.files.limits')}</p>{/if}
		</div>
		{#if list.can_manage}
			<label class="btn btn-primary btn-sm min-h-11 w-full cursor-pointer gap-2 md:min-h-0 md:w-auto {busy ? 'pointer-events-none opacity-60' : ''}">
				<Upload size={14} aria-hidden="true" />
				{busy ? t('events.files.uploading') : t('events.files.upload')}
				<input bind:this={input} type="file" multiple class="sr-only" onchange={upload} disabled={busy} />
			</label>
		{/if}
	</header>
	{#if list.attachments.length}
		<div class="overflow-x-auto border-t border-rule">
			<table class="table">
				<thead><tr><th>{t('events.files.col.name')}</th><th>{t('events.files.col.size')}</th><th>{t('events.files.col.uploaded')}</th><th></th></tr></thead>
				<tbody>
					{#each list.attachments as a (a.id)}
						<tr class="hover:bg-paper">
							<td><a class="link inline-flex items-center gap-2 font-semibold" href={a.download_url} download={a.name} data-sveltekit-reload><FileText size={14} aria-hidden="true" /> {a.name}</a></td>
							<td class="font-mono text-xs">{formatSize(a.size)}</td>
							<td class="text-sm text-muted">{formatDateTime(a.created)}{#if a.uploaded_by}<span class="block text-xs">{a.uploaded_by.name}</span>{/if}</td>
							<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1">
								<a class="btn btn-outline btn-xs gap-1" href={a.download_url} download={a.name} data-sveltekit-reload aria-label={t('events.files.download_named', { name: a.name })}><Download size={13} /> {t('events.files.download')}</a>
								{#if list.can_manage}<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('events.files.delete_named', { name: a.name })} onclick={() => ((deleting = a), (deleteOpen = true))} title={t('common.delete')}><Trash2 size={13} /></button>{/if}
							</div></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<div class="flex flex-col items-center gap-1 border-t border-rule px-5 py-10 text-center text-muted"><Paperclip size={28} aria-hidden="true" /><p>{t('events.files.none')}</p></div>
	{/if}
	</section>
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
