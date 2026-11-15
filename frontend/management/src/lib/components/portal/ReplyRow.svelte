<script lang="ts">
	import { statusLabelKey, statusStyle, formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PortalEvent } from '#lib/portal.ts';
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
	const going = $derived(event.status === 'present' || event.status === 'selected');
	const settled = $derived(event.cancelled || !canReply || going);

	async function answer(status: string, reason?: string) {
		if (await onreply(status, reason ?? event.note)) open = false;
	}
</script>

<div class="grid gap-2 py-1.5">
	<div class="flex items-center gap-3">
		<ClubCrest {name} logoUrl={photo} size={32} bg={dark ? 'var(--color-club)' : undefined} />
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm font-semibold {dark ? 'text-white' : 'text-ink'}">{name}</p>
			{#if canReply && !going && event.deadline}<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('portal.reply_by', { when: formatDateTime(event.deadline) })}</p>
			{:else if event.status === 'selected'}<p class="text-xs {dark ? 'text-on-dark' : 'text-muted'}">{t('events.status.selected')}</p>{/if}
		</div>
		<div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5 {quick ? 'w-36' : ''}">
			{#if settled}
				<span class="badge badge-sm border {event.cancelled ? 'badge-error' : statusStyle(event.status)}">{event.cancelled ? t('portal.cancelled') : t(statusLabelKey(event.status))}</span>
			{/if}
			{#if canReply}
				{#if !going}
					<button type="button" class="btn btn-sm btn-primary min-h-11" disabled={busy} onclick={() => answer('present')}>{t('portal.going')}</button>
					{#if !quick}<button type="button" class="btn btn-sm btn-outline min-h-11" disabled={busy} onclick={() => answer('maybe')}>{t('portal.maybe')}</button>{/if}
				{/if}
				<button type="button" class="btn btn-sm btn-outline min-h-11 {dark ? 'border-sidebar-chip bg-transparent text-white hover:bg-sidebar-hairline' : ''}" disabled={busy} aria-expanded={open} onclick={() => (open = !open)}>{t(going ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
			{/if}
		</div>
	</div>
	{#if canReply && open}
		<form class="grid gap-2 rounded-box border p-3 {dark ? 'border-sidebar-chip bg-sidebar-hairline' : 'border-line bg-subhead'}" onsubmit={(e) => { e.preventDefault(); void answer(event.status === 'selected' ? 'dropout' : 'absent', note || event.note); }}>
			<label class="grid gap-1 text-sm {dark ? 'text-white' : ''}">{t('portal.reason')}
				<textarea class="textarea textarea-bordered w-full text-ink" rows="2" maxlength="2000" bind:value={note} placeholder={event.note}></textarea>
			</label>
			<div class="flex flex-wrap justify-end gap-2">
				<button class="btn btn-sm btn-outline min-h-11 {dark ? 'border-sidebar-chip bg-transparent text-white' : ''}" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn btn-sm btn-primary min-h-11" type="submit" disabled={busy}>{t('common.save')}</button>
			</div>
		</form>
	{/if}
	{#if error}<p class="text-sm {dark ? 'text-white' : 'text-error'}" role="alert">{error}</p>{/if}
</div>
