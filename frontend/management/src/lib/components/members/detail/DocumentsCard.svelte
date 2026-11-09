<script lang="ts">
	import { enhance } from '$app/forms';
	import { CircleAlert, CircleCheck, Clock, Download, FileText, FileX, Plus, RotateCcw, Settings2, UserCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { documentHref, type Checklist, type ChecklistItem, type DocDisplay } from '#lib/requirements.ts';

	/**
	 * A member's documents for one season, from the requirements the club defined. Everyone who may see the member sees the status;
	 * marking received (with a file and an expiry date), "not needed", reopening and downloading need `members.manage_members`.
	 * `form` is the page's action result, for the errors of the dialogs.
	 */
	let { checklist, canManage = false, form }: { checklist: Checklist | null; canManage?: boolean; form: any } = $props();
	const items = $derived(checklist?.items ?? []);
	const membership = $derived(checklist?.membership ?? null);

	const look: Record<DocDisplay, { badge: string; tint: string; icon: typeof CircleCheck }> = {
		complete: { badge: 'badge-success', tint: 'text-ok-text', icon: CircleCheck },
		bypassed: { badge: 'badge-ghost', tint: 'text-muted', icon: UserCheck },
		expiring_soon: { badge: 'badge-warning', tint: 'text-warn-text', icon: Clock },
		expired: { badge: 'badge-error', tint: 'text-danger', icon: CircleAlert },
		missing: { badge: 'badge-ghost', tint: 'text-muted', icon: FileX }
	};
	const detail = (i: ChecklistItem) => {
		if (i.display === 'expired') return t('req.doc.d.expired', { date: formatDate(i.valid_until ?? '') });
		if (i.display === 'expiring_soon') return t('req.doc.d.expiring_soon', { date: formatDate(i.valid_until ?? ''), n: i.days_left ?? 0 });
		if (i.display === 'complete') return i.valid_until ? t('req.doc.d.valid_until', { date: formatDate(i.valid_until) }) : t('req.doc.d.complete', { date: formatDate(i.completed_at ?? '') });
		if (i.display === 'bypassed') return i.note ? `${t('req.doc.d.bypassed')}: ${i.note}` : t('req.doc.d.bypassed');
		return t('req.doc.d.missing');
	};

	let completeFor = $state<ChecklistItem | null>(null);
	let completeOpen = $state(false);
	let bypassFor = $state<ChecklistItem | null>(null);
	let bypassOpen = $state(false);
	let busy = $state(false);
	const mine = $derived(form && 'docAction' in form);
	const errText = $derived(mine && 'docError' in form && form.docError ? String(form.docError) : '');
	const fieldErr = (k: string) => (mine && form.docFields ? (form.docFields as Record<string, string>)[k] : undefined);
	const saved = $derived(form && 'docDone' in form);
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
</script>

<div class="card card-body">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div>
			<h2 class="card-title"><FileText size={18} /> {t('req.doc.title')}</h2>
			{#if membership}<p class="text-sm text-muted">{t('req.doc.subtitle', { season: membership.season.name })}{#if checklist?.total} · {t('req.doc.progress', { done: checklist.done, total: checklist.total })}{/if}</p>{/if}
		</div>
		{#if checklist?.total}
			<div class="flex flex-wrap items-center gap-2">
				{#if checklist.expired}<span class="badge badge-error badge-sm">{t('req.doc.expired_n', { n: checklist.expired })}</span>{/if}
				{#if checklist.expiring}<span class="badge badge-warning badge-sm">{t('req.doc.expiring_n', { n: checklist.expiring })}</span>{/if}
				<div class="h-2 w-40 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full bg-club" style="width: {(checklist.done / checklist.total) * 100}%"></div></div>
			</div>
		{/if}
	</div>

	{#if saved}<Alert kind="success">{t('req.doc.saved')}</Alert>{/if}
	{#if errText && !completeOpen && !bypassOpen}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}

	{#if !membership}
		<p class="mt-3 text-sm text-muted">{t('req.doc.no_membership')}</p>
	{:else if !items.length}
		<p class="mt-3 text-sm text-muted">{canManage ? t('req.doc.none_manage') : t('req.doc.none')} {#if canManage}<a class="link" href="/settings/requirements">{t('req.doc.manage_link')}</a>{/if}</p>
	{:else}
		<ul class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
			{#each items as i (i.requirement.id)}
				{@const l = look[i.display]}
				<li class="grid gap-2 rounded-lg border border-line p-3.5">
					<div class="flex items-center gap-3">
						<span class="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface {l.tint}"><l.icon size={20} /></span>
						<div class="min-w-0 flex-1">
							<p class="truncate font-semibold text-ink" title={i.requirement.description}>{i.requirement.name}</p>
							<p class="truncate text-xs text-muted">{detail(i)}</p>
						</div>
						<span class="badge badge-sm shrink-0 {l.badge}">{t(`req.doc.status.${i.display}`)}</span>
					</div>
					{#if canManage}
						<div class="flex flex-wrap justify-end gap-1.5">
							{#if i.has_document}<a class="btn btn-outline btn-xs gap-1" href={documentHref(membership.id, i.requirement.id)} target="_blank" rel="noopener"><Download size={13} /> {t('req.doc.download')}</a>{/if}
							{#if i.display !== 'missing'}
								<form method="POST" action="?/reopenRequirement" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.reopen_confirm'))) cancel(); }}>
									<input type="hidden" name="membership_id" value={membership.id} />
									<input type="hidden" name="requirement_id" value={i.requirement.id} />
									<button class="btn btn-outline btn-error btn-xs gap-1" type="submit"><RotateCcw size={13} /> {t('req.doc.reopen')}</button>
								</form>
							{/if}
							{#if i.display === 'missing' || i.display === 'expired'}<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => ((bypassFor = i), (bypassOpen = true))}><UserCheck size={13} /> {t('req.doc.bypass')}</button>{/if}
							<button class="btn btn-primary btn-xs gap-1" type="button" onclick={() => ((completeFor = i), (completeOpen = true))}><Plus size={13} /> {i.display === 'complete' || i.display === 'expiring_soon' ? t('req.doc.renew') : t('req.doc.receive')}</button>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
		{#if canManage}<p class="mt-3 text-sm"><a class="link inline-flex items-center gap-1.5 font-semibold" href="/settings/requirements"><Settings2 size={14} aria-hidden="true" /> {t('req.doc.manage_link')}</a></p>{/if}
	{/if}
</div>

{#if membership}
	<Modal bind:open={completeOpen} title={t('req.doc.complete_title', { name: completeFor?.requirement.name ?? '' })}>
		<form method="POST" action="?/completeRequirement" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit(() => (completeOpen = false))}>
			<input type="hidden" name="membership_id" value={membership.id} />
			<input type="hidden" name="requirement_id" value={completeFor?.requirement.id ?? ''} />
			{#if errText}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}
			<div class="form-control">
				<label class="label" for="doc-file"><span class="label-text">{t('req.doc.file')}{#if completeFor?.requirement.requires_document && !completeFor?.has_document}<span class="text-error" aria-hidden="true"> *</span>{/if}</span></label>
				<input id="doc-file" name="document" type="file" class="file-input file-input-bordered w-full {fieldErr('document') ? 'file-input-error' : ''}" />
				<span class="label-text-alt mt-1 block">{completeFor?.has_document ? t('req.doc.file_keep') : completeFor?.requirement.requires_document ? t('req.doc.file_required') : t('req.doc.file_help')}</span>
				{#if fieldErr('document')}<span class="mt-1 block text-xs text-error">{fieldErr('document')}</span>{/if}
			</div>
			<div class="form-control">
				<label class="label" for="doc-until"><span class="label-text">{t('req.doc.valid_until')}</span></label>
				<input id="doc-until" name="valid_until" type="date" class="input input-bordered w-full {fieldErr('valid_until') ? 'input-error' : ''}" />
				<span class="label-text-alt mt-1 block">{completeFor?.requirement.valid_for_months ? t('req.doc.valid_until_default', { n: completeFor.requirement.valid_for_months }) : t('req.doc.valid_until_help')}</span>
				{#if fieldErr('valid_until')}<span class="mt-1 block text-xs text-error">{fieldErr('valid_until')}</span>{/if}
			</div>
			<div class="form-control">
				<label class="label" for="doc-note"><span class="label-text">{t('req.doc.note')}</span></label>
				<textarea id="doc-note" name="note" class="textarea textarea-bordered w-full" rows="2"></textarea>
				<span class="label-text-alt mt-1 block">{t('req.doc.note_help')}</span>
			</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (completeOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={busy}>{t('req.doc.save')}</button>
			</div>
		</form>
	</Modal>

	<Modal bind:open={bypassOpen} title={t('req.doc.bypass_title', { name: bypassFor?.requirement.name ?? '' })}>
		<form method="POST" action="?/bypassRequirement" class="grid gap-3" use:enhance={submit(() => (bypassOpen = false))}>
			<input type="hidden" name="membership_id" value={membership.id} />
			<input type="hidden" name="requirement_id" value={bypassFor?.requirement.id ?? ''} />
			{#if errText && !fieldErr('note')}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}
			<div class="form-control">
				<label class="label" for="doc-bypass-note"><span class="label-text">{t('req.doc.bypass_note')} <span class="text-error" aria-hidden="true">*</span></span></label>
				<textarea id="doc-bypass-note" name="note" class="textarea textarea-bordered w-full {fieldErr('note') ? 'textarea-error' : ''}" rows="2" required></textarea>
				{#if fieldErr('note')}<span class="mt-1 block text-xs text-error">{fieldErr('note')}</span>{/if}
			</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (bypassOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={busy}>{t('req.doc.save')}</button>
			</div>
		</form>
	</Modal>
{/if}
