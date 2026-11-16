<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { SendStatus } from '#lib/forms.ts';

	let { status, small = false }: { status: SendStatus; small?: boolean } = $props();
	const tone = $derived({ open: 'badge-success', scheduled: 'badge-info', closed: '', inactive: 'badge-warning' }[status]);
</script>

<span class="badge {tone} {small ? 'badge-sm' : ''}">{t(`forms.status.${status}`)}</span>
