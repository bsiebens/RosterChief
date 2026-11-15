<script lang="ts">
	import { Whistle } from '@lucide/svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { OfficiatingOpportunity } from '#lib/portal.ts';

	/** A request to referee or officiate, answered in place: compact buttons on the right, the same size as the player reply buttons. */
	let { offer, name, busy = false, error = '', onreply }: { offer: OfficiatingOpportunity; name: string; busy?: boolean; error?: string; onreply: (available: boolean) => void } = $props();
	const open = $derived(offer.signup_open && offer.status !== 'assigned');
</script>

<div class="grid gap-1 py-1.5">
	<div class="flex items-center gap-3">
		<Whistle size={18} class="shrink-0 text-muted" aria-hidden="true" />
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm font-semibold text-ink">{t(`portal.crew_${offer.role}`)} · {name}</p>
			{#if open && offer.deadline}<p class="text-xs text-muted">{t('portal.reply_by', { when: formatDateTime(offer.deadline) })}</p>{/if}
		</div>
		<div class="flex shrink-0 flex-nowrap items-center justify-end gap-1.5">
			<span class="badge badge-sm border md:h-8 md:px-3 {offer.status === 'assigned' || offer.status === 'signed_up' ? 'badge-success' : offer.status === 'declined' ? 'badge-outline' : 'badge-outline'}" role="status">{t(offer.status === 'invited' && !offer.signup_open ? 'portal.crew_closed' : `portal.crew_${offer.status}`)}</span>
			{#if open}
				{#if offer.status !== 'signed_up'}<button type="button" class="btn btn-sm btn-primary min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => onreply(true)}>{t('portal.crew_volunteer')}</button>{/if}
				{#if offer.status !== 'declined'}<button type="button" class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => onreply(false)}>{t(offer.status === 'signed_up' ? 'portal.crew_withdraw' : 'portal.crew_decline')}</button>{/if}
			{/if}
		</div>
	</div>
	{#if error}<p role="alert" class="text-sm text-error">{error}</p>{/if}
</div>
