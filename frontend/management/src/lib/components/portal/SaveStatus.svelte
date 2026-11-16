<script lang="ts">
	import { Check, CircleAlert, LoaderCircle } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Says whether a change is being saved. By default a banner across the top of the screen that shows while saving, confirms the save and
	 * leaves again by itself (the page sets the status back to idle); `compact` is the small inline version next to a Save button, which
	 * can also show that edits are not saved yet.
	 */
	let { status, compact = false, dirty = false }: { status: 'idle' | 'saving' | 'saved' | 'error'; compact?: boolean; dirty?: boolean } = $props();
</script>

{#if compact}
	<p class="flex items-center gap-2 text-sm {status === 'saved' ? 'text-ok-text' : status === 'error' ? 'text-danger' : 'text-muted'}" role="status" aria-live="polite">
		{#if status === 'saving'}<LoaderCircle size={15} class="animate-spin" aria-hidden="true" />{t('portal.saving')}
		{:else if status === 'saved'}<Check size={15} aria-hidden="true" />{t('portal.saved_all')}
		{:else if status === 'error'}<CircleAlert size={15} aria-hidden="true" />{t('portal.save_failed')}
		{:else if dirty}<span class="size-2 rounded-full bg-warn"></span>{t('portal.unsaved')}{/if}
	</p>
{:else}
	<div class="pointer-events-none fixed inset-x-0 top-3 z-50 flex justify-center px-4" role="status" aria-live="polite">
		{#if status !== 'idle'}
			<p class="pointer-events-auto flex items-center gap-2 rounded-box border px-4 py-2.5 text-sm font-semibold shadow-lg {status === 'saved' ? 'border-ok-border bg-ok-bg text-ok-text' : status === 'error' ? 'border-danger bg-error-bg text-danger' : 'border-info-border bg-info-bg text-info-text'}">
				{#if status === 'saving'}<LoaderCircle size={16} class="animate-spin" aria-hidden="true" />{t('portal.saving')}
				{:else if status === 'saved'}<Check size={16} aria-hidden="true" />{t('portal.saved_all')}
				{:else}<CircleAlert size={16} aria-hidden="true" />{t('portal.save_failed')}{/if}
			</p>
		{/if}
	</div>
{/if}
