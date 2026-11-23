<script lang="ts">
	import { Flame, LoaderCircle, Paperclip, Send } from '@lucide/svelte';
	import { attachProblem, canSend, errorText, type Attachment } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { ApiCall } from '#lib/management-api.ts';
	import AttachmentChip from './AttachmentChip.svelte';

	/**
	 * The message box: text, an attach button (images and PDFs, uploaded straight away and shown as removable chips), a send button and, for
	 * chat admins of an announcement chat, an "Urgent" checkbox. Enter sends on a computer, on a touch screen it starts a new line.
	 */
	let { onsend, onupload, canUrgent = false }: {
		onsend: (body: string, attachments: Attachment[], urgent: boolean) => void;
		onupload: (file: File) => Promise<ApiCall<Attachment>>;
		canUrgent?: boolean;
	} = $props();

	let body = $state('');
	let attachments = $state<Attachment[]>([]);
	let uploading = $state(0);
	let urgent = $state(false);
	let error = $state('');
	let input: HTMLTextAreaElement | undefined = $state();
	let picker: HTMLInputElement | undefined = $state();
	const ready = $derived(canSend(body, attachments.length, uploading > 0));

	function grow() {
		if (!input) return;
		input.style.height = 'auto';
		input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
	}

	function submit() {
		if (!ready) return;
		onsend(body, attachments, urgent && canUrgent);
		body = '';
		attachments = [];
		urgent = false;
		error = '';
		queueMicrotask(grow);
		input?.focus();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
		if (typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches) return;
		e.preventDefault();
		submit();
	}

	async function pick(e: Event) {
		const el = e.currentTarget as HTMLInputElement;
		const files = [...(el.files ?? [])];
		el.value = '';
		error = '';
		for (const file of files) {
			const problem = attachProblem(file);
			if (problem) {
				error = t(problem);
				continue;
			}
			uploading++;
			const res = await onupload(file);
			uploading--;
			if (res.ok && res.data) attachments = [...attachments, res.data];
			else error = errorText(res, t('chat.err.upload'));
		}
	}
</script>

<form class="grid gap-2 border-t border-line bg-white p-2.5 sm:p-3" onsubmit={(e) => { e.preventDefault(); submit(); }}>
	{#if error}<p class="text-xs text-danger" role="alert">{error}</p>{/if}
	{#if attachments.length || uploading}
		<div class="flex flex-wrap gap-1.5">
			{#each attachments as a (a.id)}<AttachmentChip attachment={a} onremove={() => (attachments = attachments.filter((x) => x.id !== a.id))} />{/each}
			{#if uploading}<span class="inline-flex items-center gap-1.5 px-2 text-xs text-muted" role="status"><LoaderCircle class="animate-spin" size={14} aria-hidden="true" />{t('chat.composer.uploading')}</span>{/if}
		</div>
	{/if}
	<div class="flex items-end gap-1.5">
		<input bind:this={picker} class="sr-only" type="file" accept="image/*,application/pdf" multiple tabindex="-1" aria-hidden="true" onchange={pick} />
		<button type="button" class="btn btn-ghost btn-square shrink-0" aria-label={t('chat.composer.attach')} title={t('chat.composer.attach')} onclick={() => picker?.click()}><Paperclip size={18} aria-hidden="true" /></button>
		<textarea bind:this={input} bind:value={body} oninput={grow} {onkeydown} class="textarea max-h-40 min-h-9 min-w-0 flex-1 resize-none py-2" rows="1" maxlength="4000" placeholder={t('chat.composer.placeholder')} aria-label={t('chat.composer.label')}></textarea>
		<button type="submit" class="btn btn-primary btn-square shrink-0" aria-label={t('chat.composer.send')} title={t('chat.composer.send')} disabled={!ready}><Send size={17} aria-hidden="true" /></button>
	</div>
	{#if canUrgent}
		<label class="flex items-center gap-2 px-1 text-xs text-muted">
			<input type="checkbox" class="checkbox checkbox-sm" bind:checked={urgent} />
			<Flame size={13} class="text-danger" aria-hidden="true" /><b class="text-ink">{t('chat.composer.urgent')}</b> {t('chat.composer.urgent_help')}
		</label>
	{/if}
</form>
