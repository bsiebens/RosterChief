<script lang="ts">
	import { statusLabelKey, statusStyle, formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PortalEvent } from '#lib/portal.ts';
	import { CircleAlert, MessageSquareText } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';

	/**
	 * One person's answer to one event: avatar and name on the left, their state or the reply buttons in a fixed slot on the right
	 * (so rows in a list end on the same edge), and the optional reason form underneath. `quick` is the short form (Going / Can't, no Maybe)
	 * used on the home hero; `dark` is for the dark hero panel. `onreply` resolves to whether it worked, which closes the reason form.
	 */
	let { event, name, photo = null, canReply, quick = false, dark = false, seg = false, busy = false, error = '', onreply }: {
		event: PortalEvent;
		name: string;
		photo?: string | null;
		canReply: boolean;
		quick?: boolean;
		dark?: boolean;
		/** The event sheet layout: a three-way Going / Maybe / Can't switch, the reason when it is a no, and a Send button. */
		seg?: boolean;
		busy?: boolean;
		error?: string;
		onreply: (status: string, note: string) => Promise<boolean>;
	} = $props();

	let open = $state(false);
	let note = $state('');
	// The answer given shows at once and stays until the refreshed data is in, so the old state never flashes back.
	let pending = $state<string | null>(null);
	const shown = $derived(pending ?? event.status);
	const answeredNo = $derived(['maybe', 'absent', 'excused', 'dropout'].includes(shown));
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
	// A reason is required: ask for it right here, next to the field, instead of a loose line below the form.
	let localError = $state('');
	const reasonError = $derived(localError || error);
	function submitReason() {
		if (!(note || event.note).trim()) {
			localError = t('portal.reason_required');
			document.getElementById(`reason-${event.id}`)?.focus();
			return;
		}
		void answer(event.status === 'selected' ? 'dropout' : 'absent', note || event.note);
	}
	const lateInstalment = $derived(event.blocked === 'late_installment' && !going);
	const settled = $derived(event.cancelled || !canReply || going);

	// The staged choice of the switch layout: nothing is sent until "Send reply".
	const current = $derived<'' | 'present' | 'maybe' | 'absent'>(going ? 'present' : shown === 'maybe' ? 'maybe' : ['absent', 'excused', 'dropout'].includes(shown) ? 'absent' : '');
	let choice = $state<'' | 'present' | 'maybe' | 'absent' | null>(null);
	const picked = $derived(choice ?? current);
	const switchable = $derived(seg && canReply && !noInvite && !lateInstalment && !event.cancelled);
	const changed = $derived(picked !== '' && (picked !== current || (picked === 'absent' && note.trim() !== '' && note.trim() !== event.note)));
	const SWITCH = [
		{ key: 'present', label: 'portal.going', on: 'bg-ok text-white' },
		{ key: 'maybe', label: 'portal.maybe', on: 'bg-warn-bg text-warn-text ring-2 ring-warn-border' },
		{ key: 'absent', label: 'portal.cant_short', on: 'bg-club text-club-content' }
	] as const;
	function send() {
		if (picked === 'absent') return submitReason();
		if (picked) void answer(picked);
	}

	async function answer(status: string, reason?: string) {
		pending = status;
		const ok = await onreply(status, reason ?? event.note);
		pending = null;
		if (ok) {
			open = false;
			choice = null;
		}
	}
</script>

{#if switchable}
	<div class="grid min-w-0 gap-2.5 py-2">
		<div class="flex items-center gap-3">
			<ClubCrest {name} logoUrl={photo} size={32} />
			<div class="min-w-0 flex-1">
				<p class="truncate font-semibold text-ink">{t('portal.is_coming', { name })}</p>
				{#if event.deadline && !going}<p class="text-xs text-muted">{t('portal.reply_by', { when: formatDateTime(event.deadline) })}</p>{/if}
			</div>
		</div>
		<div class="grid grid-cols-3 gap-1 rounded-box bg-subhead p-1" role="group" aria-label={t('portal.reply')}>
			{#each SWITCH as option (option.key)}
				<button type="button" class="min-h-13 rounded-box text-base font-semibold {picked === option.key ? option.on : 'text-muted'}" aria-pressed={picked === option.key} disabled={busy} onclick={() => (choice = option.key)}>{t(option.label)}</button>
			{/each}
		</div>
		{#if picked === 'absent'}
			<div class="grid gap-2">
				<p class="text-sm font-semibold text-ink">{t('portal.reason_title')}</p>
				<div class="grid grid-cols-4 gap-1.5">
					{#each REASONS as key (key)}
						<button type="button" class="portal-person compact text min-w-0 justify-center !px-1" aria-pressed={note === t(key)} onclick={() => (note = note === t(key) ? '' : t(key))}>{t(key)}</button>
					{/each}
				</div>
				<textarea id="reason-{event.id}" class="textarea textarea-bordered w-full text-ink {reasonError ? 'border-danger! ring-2 ring-danger/40' : ''}" rows="2" maxlength="2000" aria-label={t('portal.reason')} bind:value={note} placeholder={event.note || t('portal.reason_help')}></textarea>
				{#if reasonError}<p class="text-sm font-semibold text-danger" role="alert">{reasonError}</p>{/if}
			</div>
		{/if}
		<button type="button" class="btn btn-primary min-h-12 w-full" disabled={busy || !changed} onclick={send}>{t('portal.send_reply')}</button>
		<p class="text-xs text-muted">{t('portal.reply_change_help')}</p>
		{#if error && picked !== 'absent'}<p class="text-sm text-error" role="alert">{error}</p>{/if}
	</div>
{:else}
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
						<button type="button" class="btn btn-sm min-h-11 md:h-8 md:min-h-0 {answeredNo ? 'btn-outline' : 'btn-primary'}" disabled={busy} onclick={() => answer('present')}>{t('portal.going')}</button>
						{#if !quick}<button type="button" class="btn btn-sm min-h-11 md:h-8 md:min-h-0 {shown === 'maybe' ? 'btn-warning' : 'btn-outline'}" aria-pressed={shown === 'maybe'} disabled={busy} onclick={() => answer('maybe')}>{t('portal.maybe')}</button>{/if}
					{/if}
				{/if}
				{#if canReply}
					<button type="button" class="btn btn-sm min-h-11 md:h-8 md:min-h-0 {['absent', 'excused', 'dropout'].includes(shown) ? '' : 'btn-outline'} {dark ? 'border-sidebar-chip bg-transparent! text-sidebar-fg hover:bg-sidebar-hairline' : ''}" disabled={busy} aria-expanded={open} onclick={() => (open = !open)}>{t(going ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
				{/if}
			{/if}
		</div>
	</div>
	{#if lateInstalment && canReply}<p class="rounded-box px-3 py-2 text-xs {dark ? 'bg-sidebar-hairline text-sidebar-fg' : 'bg-warn-bg text-warn-text'}" role="status">{t('portal.late')}</p>{/if}
	{#if canReply && open && !noInvite}
		<form use:reveal class="grid gap-3 rounded-box border p-3.5 {dark ? 'border-sidebar-chip bg-sidebar-hairline' : 'border-line bg-subhead'}" onsubmit={(e) => { e.preventDefault(); submitReason(); }}>
			<div>
				<p class="flex items-center gap-2 font-display text-base font-extrabold uppercase {dark ? 'text-sidebar-fg' : 'text-ink'}"><MessageSquareText size={16} class="shrink-0" aria-hidden="true" />{t('portal.reason_title')}</p>
				<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('portal.reason_help')}</p>
			</div>
			<div class="grid grid-cols-4 gap-1.5">
				{#each REASONS as key (key)}
					<button type="button" class="portal-person compact text min-w-0 justify-center !px-1" aria-pressed={note === t(key)} onclick={() => (note = note === t(key) ? '' : t(key))}>{t(key)}</button>
				{/each}
			</div>
			<textarea id="reason-{event.id}" class="textarea textarea-bordered w-full text-ink {reasonError ? 'border-danger! ring-2 ring-danger/40' : ''}" rows="3" maxlength="2000" aria-label={t('portal.reason')} aria-invalid={reasonError ? 'true' : undefined} aria-describedby={reasonError ? `reason-err-${event.id}` : undefined} bind:value={note} oninput={() => (localError = '')} placeholder={event.note || t('portal.reason_placeholder')}></textarea>
			{#if reasonError}<p id="reason-err-{event.id}" class="flex items-start gap-2 rounded-box border border-danger-border bg-danger-bg px-3 py-2 text-sm font-semibold text-danger" role="alert"><CircleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />{reasonError}</p>{/if}
			<div class="flex flex-wrap justify-end gap-2">
				<button class="btn btn-sm btn-outline min-h-11 md:h-8 md:min-h-0 {dark ? 'border-sidebar-chip bg-transparent! text-sidebar-fg' : ''}" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn btn-sm btn-primary min-h-11 flex-1 sm:flex-none md:h-8 md:min-h-0" type="submit" disabled={busy}>{t(going ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
			</div>
		</form>
	{/if}
	{#if error && !(canReply && open && !noInvite)}<p class="text-sm {dark ? 'text-sidebar-fg' : 'text-error'}" role="alert">{error}</p>{/if}
</div>
{/if}
