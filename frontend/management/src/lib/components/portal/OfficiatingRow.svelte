<script lang="ts">
	import { formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { OfficiatingOpportunity } from '#lib/portal.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';

	/**
	 * A request to referee or officiate, answered in place: the person's picture and name, what they are being asked underneath, and the
	 * compact buttons on the right (the size of the player reply buttons). A state badge only shows once there is one to show.
	 */
	let { offer, name, photo = null, busy = false, error = '', onreply }: { offer: OfficiatingOpportunity; name: string; photo?: string | null; busy?: boolean; error?: string; onreply: (available: boolean) => void } = $props();
	const open = $derived(offer.signup_open && offer.status !== 'assigned');
	const state = $derived(offer.status === 'invited' ? (offer.signup_open ? '' : t('portal.crew_closed')) : t(`portal.crew_${offer.status}`));
</script>

<div class="grid gap-1 py-1.5">
	<div class="flex items-center gap-3">
		<ClubCrest {name} logoUrl={photo} size={32} />
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm font-semibold text-ink">{name}</p>
			<p class="text-xs text-muted">{t(offer.role === 'referee' ? 'portal.crew_ask_referee' : 'portal.crew_ask_official')}{#if open && offer.deadline} · {t('portal.reply_by', { when: formatDateTime(offer.deadline) })}{/if}</p>
		</div>
		<div class="flex shrink-0 flex-nowrap items-center justify-end gap-1.5">
			{#if state}<span class="badge badge-sm border md:h-8 md:px-3 {offer.status === 'assigned' || offer.status === 'signed_up' ? 'badge-success' : 'badge-outline'}" role="status">{state}</span>{/if}
			{#if open}
				{#if offer.status !== 'signed_up'}<button type="button" class="btn btn-sm btn-primary min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => onreply(true)}>{t('portal.crew_volunteer')}</button>{/if}
				{#if offer.status !== 'declined'}<button type="button" class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => onreply(false)}>{t(offer.status === 'signed_up' ? 'portal.crew_withdraw' : 'portal.crew_decline')}</button>{/if}
			{/if}
		</div>
	</div>
	{#if error}<p role="alert" class="text-sm text-error">{error}</p>{/if}
</div>
