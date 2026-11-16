<script lang="ts">
	import { FlaskConical } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Marks a block that is drawn from the approved design with sample content because the backend for it does not exist yet.
	 * `what` names the block for the handover list (docs/redesign-backend-prompt.md): grep for `<SampleBadge` to find every one.
	 * Remove the badge, and the fixture it reads from lib/portal-sample.ts, when the block is wired to live data.
	 */
	let { what }: { what: string } = $props();
</script>

<span class="badge badge-sm badge-outline inline-flex items-center gap-1 font-mono tracking-wide uppercase" title={t('portal.sample_help')} data-sample={what}><FlaskConical size={11} aria-hidden="true" />{t('portal.sample')}</span>
