<script lang="ts">
	import { Check, CircleAlert, LoaderCircle } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** The one place that says whether a screen saves by itself and whether the last change went through. */
	let { status, compact = false, dirty = false }: { status: 'idle' | 'saving' | 'saved' | 'error'; compact?: boolean; dirty?: boolean } = $props();
</script>

<p class="flex items-center gap-2 text-sm {status === 'saved' ? 'text-ok-text' : status === 'error' ? 'text-danger' : 'text-muted'}" role="status" aria-live="polite">
	{#if status === 'saving'}<LoaderCircle size={15} class="animate-spin" aria-hidden="true" />{t('portal.saving')}
	{:else if status === 'saved'}<Check size={15} aria-hidden="true" />{t('portal.saved_all')}
	{:else if status === 'error'}<CircleAlert size={15} aria-hidden="true" />{t('portal.save_failed')}
	{:else if dirty}<span class="size-2 rounded-full bg-warn"></span>{t('portal.unsaved')}
	{:else if !compact}{t('portal.autosave')}{/if}
</p>
