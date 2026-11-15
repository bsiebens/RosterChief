<script lang="ts">
	import { statusLabelKey, statusStyle, formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PortalEvent } from '#lib/portal.ts';
	import { MessageSquareText } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';

	/**
	 * One person's answer to one event: avatar and name on the left, their state or the reply buttons in a fixed slot on the right
	 * (so rows in a list end on the same edge), and the optional reason form underneath. `quick` is the short form (Going / Can't, no Maybe)
	 * used on the home hero; `dark` is for the dark hero panel. `onreply` resolves to whether it worked, which closes the reason form.
	 */
	let { event, name, photo = null, canReply, quick = false, dark = false, busy = false, error = '', onreply }: {
		event: PortalEvent;
		name: string;
		photo?: string | null;
		canReply: boolean;
		quick?: boolean;
		dark?: boolean;
		busy?: boolean;
		error?: string;
		onreply: (status: string, note: string) => Promise<boolean>;
	} = $props();

	let open = $state(false);
	let note = $state('');
	// The answer given shows at once and stays until the refreshed data is in, so the old state never flashes back.
	let pending = $state<string | null>(null);
	const shown = $derived(pending ?? event.status);
	const going = $derived(shown === 'present' || shown === 'selected');
	const noInvite = $derived(event.blocked === 'not_invited');
	const noInviteWhy = $derived.by(() => {
		const detail = event.blocked_detail;
		if (detail?.reason === 'requirements' && detail.requirements.length) return t('portal.not_invited_requirements', { list: detail.requirements.join(', ') });
		return t(detail?.reason === 'excluded' ? 'portal.not_invited_excluded' : detail?.reason === 'not_in_audience' ? 'portal.not_invited_audience' : 'portal.not_invited');
	});
	const REASONS = ['portal.reason_sick', 'portal.reason_injured', 'portal.reason_school', 'portal.reason_other'];
	// The reason form takes the eye: it scrolls into view and puts the cursor in the field.
	function reveal(node: HTMLElement) {
		node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		node.querySelector('textarea')?.focus({ preventScroll: true });
	}
	const lateInstalment = $derived(event.blocked === 'late_installment' && !going);
	const settled = $derived(event.cancelled || !canReply || going);

	async function answer(status: string, reason?: string) {
		pending = status;
		const ok = await onreply(status, reason ?? event.note);
		pending = null;
		if (ok) open = false;
	}
</script>

<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 py-1.5">
	<div class="flex items-center gap-3">
		<ClubCrest {name} logoUrl={photo} size={32} bg={dark ? 'var(--color-club)' : undefined} />
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm font-semibold {dark ? 'text-sidebar-fg' : 'text-ink'}">{name}</p>
			{#if noInvite}<p class="text-xs leading-snug {dark ? 'text-on-dark' : 'text-muted'}">{noInviteWhy}</p>
			{:else if canReply && !going && event.deadline}<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('portal.reply_by', { when: formatDateTime(event.deadline) })}</p>
			{:else if event.status === 'selected'}<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('events.status.selected')}</p>{/if}
		</div>
		<div class="flex shrink-0 flex-nowrap items-center justify-end gap-1.5">
				{#if !noInvite}
				{#if settled}
					<span class="badge badge-sm border md:h-8 md:px-3 {event.cancelled ? 'badge-error' : statusStyle(shown)}">{event.cancelled ? t('portal.cancelled') : t(statusLabelKey(shown))}</span>
				{/if}
				{#if canReply && !lateInstalment}
					{#if !going}
						<button type="button" class="btn btn-sm btn-primary min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => answer('present')}>{t('portal.going')}</button>
						{#if !quick}<button type="button" class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0" disabled={busy} onclick={() => answer('maybe')}>{t('portal.maybe')}</button>{/if}
					{/if}
				{/if}
				{#if canReply}
					<button type="button" class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0 {dark ? 'border-sidebar-chip bg-transparent! text-sidebar-fg hover:bg-sidebar-hairline' : ''}" disabled={busy} aria-expanded={open} onclick={() => (open = !open)}>{t(going ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
				{/if}
			{/if}
		</div>
	</div>
	{#if lateInstalment && canReply}<p class="rounded-box px-3 py-2 text-xs {dark ? 'bg-sidebar-hairline text-sidebar-fg' : 'bg-warn-bg text-warn-text'}" role="status">{t('portal.late')}</p>{/if}
	{#if canReply && open && !noInvite}
		<form use:reveal class="grid gap-3 rounded-box border-2 border-club p-3.5 {dark ? 'bg-sidebar-hairline' : 'bg-subhead'}" onsubmit={(e) => { e.preventDefault(); void answer(event.status === 'selected' ? 'dropout' : 'absent', note || event.note); }}>
			<div>
				<p class="flex items-center gap-2 font-display text-base font-extrabold uppercase {dark ? 'text-sidebar-fg' : 'text-ink'}"><MessageSquareText size={16} class="shrink-0 text-club" aria-hidden="true" />{t('portal.reason_title')}</p>
				<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('portal.reason_help')}</p>
			</div>
			<div class="flex flex-wrap gap-1.5">
				{#each REASONS as key (key)}
					<button type="button" class="portal-person compact text" aria-pressed={note === t(key)} onclick={() => (note = note === t(key) ? '' : t(key))}>{t(key)}</button>
				{/each}
			</div>
			<textarea id="reason-{event.id}" class="textarea textarea-bordered w-full text-ink" rows="3" maxlength="2000" aria-label={t('portal.reason')} bind:value={note} placeholder={event.note || t('portal.reason_placeholder')}></textarea>
			<div class="flex flex-wrap justify-end gap-2">
				<button class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0 {dark ? 'border-sidebar-chip bg-transparent! text-sidebar-fg' : ''}" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn btn-sm btn-primary min-h-11 flex-1 sm:flex-none md:h-8 md:min-h-0" type="submit" disabled={busy}>{t(going ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
			</div>
		</form>
	{/if}
	{#if error}<p class="text-sm {dark ? 'text-sidebar-fg' : 'text-error'}" role="alert">{error}</p>{/if}
</div>
