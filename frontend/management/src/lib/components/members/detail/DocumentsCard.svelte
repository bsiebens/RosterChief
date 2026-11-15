<script lang="ts">
	import { enhance } from '$app/forms';
	import { CircleAlert, CircleCheck, CircleQuestionMark, Clock, Download, FileText, FileX, Hourglass, Pencil, Plus, RotateCcw, Settings2, UserCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import AnswerInput from '#lib/components/requirements/AnswerInput.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { answerText, documentHref, type Checklist, type ChecklistItem, type DocDisplay } from '#lib/requirements.ts';

	/**
	 * A member's documents and questions for one season, from the requirements the club defined (a question shows its answer; staff can answer it). Everyone who may see the member sees the status;
	 * marking received (with a file and an expiry date), "not needed", reopening and downloading need `members.manage_members`.
	 * `form` is the page's action result, for the errors of the dialogs.
	 */
	let { checklist, canManage = false, form, actionBase = '?tab=documents&' }: { checklist: Checklist | null; canManage?: boolean; form: any; /** Where the form actions live: `?tab=documents&` on the member page, `?` on a page that spreads `requirementActions`. */ actionBase?: string } = $props();
	const items = $derived(checklist?.items ?? []);
	const membership = $derived(checklist?.membership ?? null);

	const look: Record<DocDisplay, { badge: string; tint: string; icon: typeof CircleCheck }> = {
		complete: { badge: 'badge-success', tint: 'text-ok-text', icon: CircleCheck },
		bypassed: { badge: 'badge-ghost', tint: 'text-muted', icon: UserCheck },
		expiring_soon: { badge: 'badge-warning', tint: 'text-warn-text', icon: Clock },
		expired: { badge: 'badge-error', tint: 'text-danger', icon: CircleAlert },
		missing: { badge: 'badge-ghost', tint: 'text-muted', icon: FileX },
		pending: { badge: 'badge-info', tint: 'text-info-text', icon: Hourglass }
	};
	const isQuestion = (i: ChecklistItem) => i.requirement.kind === 'question';
	const shownAnswer = (i: ChecklistItem) => answerText(i.requirement, i.answer, t('common.yes'), t('common.no'), formatDate);
	const detail = (i: ChecklistItem) => {
		if (i.display === 'pending') return isQuestion(i) ? `${shownAnswer(i)} · ${t('req.doc.d.pending')}` : t('req.doc.d.pending');
		if (isQuestion(i) && i.display !== 'bypassed') {
			if (i.display === 'missing') return t('req.doc.q.missing');
			const base = shownAnswer(i);
			if (i.display === 'expired') return `${base} · ${t('req.doc.d.expired', { date: formatDate(i.valid_until ?? '') })}`;
			if (i.display === 'expiring_soon') return `${base} · ${t('req.doc.d.expiring_soon', { date: formatDate(i.valid_until ?? ''), n: i.days_left ?? 0 })}`;
			return base;
		}
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
	let answerFor = $state<ChecklistItem | null>(null);
	let answerOpen = $state(false);
	let busy = $state(false);
	const mine = $derived(form && 'docAction' in form);
	const errText = $derived(mine && 'docError' in form && form.docError ? String(form.docError) : '');
	const fieldErr = (k: string) => (mine && form.docFields ? (form.docFields as Record<string, string>)[k] : undefined);
	const saved = $derived(form && 'docDone' in form);
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			if (result.type === 'success') close(); // before the reload, so the dialog never lingers
			await update({ reset: false });
			busy = false;
		};
	};
</script>

<div class="card card-body">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div>
			<h2 class="card-title"><FileText size={18} /> {t('req.doc.title')}</h2>
			{#if membership}<p class="text-sm text-muted">{t('req.doc.subtitle', { season: membership.season.name })}{#if checklist?.total}&nbsp;·&nbsp;<span class="font-semibold {checklist.expired ? 'text-danger' : checklist.done === checklist.total ? 'text-ok-text' : 'text-ink'}">{t('req.doc.progress', { done: checklist.done, total: checklist.total })}</span>{/if}</p>{/if}
		</div>
		{#if checklist?.total}
			<div class="flex flex-wrap items-center gap-2">
				{#if checklist.expired}<span class="badge badge-error badge-sm">{t('req.doc.expired_n', { n: checklist.expired })}</span>{/if}
				{#if checklist.expiring}<span class="badge badge-warning badge-sm">{t('req.doc.expiring_n', { n: checklist.expiring })}</span>{/if}
				<div class="h-2 w-40 overflow-hidden rounded-full bg-rule"><div class="h-full rounded-full {checklist.expired ? 'bg-danger' : checklist.done === checklist.total ? 'bg-ok' : 'bg-club'}" style="width: {(checklist.done / checklist.total) * 100}%"></div></div>
			</div>
		{/if}
	</div>

	{#if saved}<Alert kind="success">{t('req.doc.saved')}</Alert>{/if}
	{#if errText && !completeOpen && !bypassOpen && !answerOpen}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}

	{#if !membership}
		<p class="mt-3 text-sm text-muted">{t('req.doc.no_membership')}</p>
	{:else if !items.length}
		<p class="mt-3 text-sm text-muted">{canManage ? t('req.doc.none_manage') : t('req.doc.none')} {#if canManage}<a class="link" href="/settings/requirements">{t('req.doc.manage_link')}</a>{/if}</p>
	{:else}
		<ul class="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
			{#each items as i (i.requirement.id)}
				{@const l = look[i.display]}
				<li class="rounded-lg border border-line p-3.5">
					<div class="flex flex-wrap items-center gap-x-4 gap-y-3">
						<div class="flex min-w-0 flex-1 basis-48 items-center gap-3">
							<span class="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface {l.tint}">
								{#if isQuestion(i) && i.display === 'missing'}<CircleQuestionMark size={20} />{:else}<l.icon size={20} />{/if}
							</span>
							<div class="min-w-0 flex-1">
								<p class="truncate font-semibold text-ink" title={i.requirement.description}>{i.requirement.name}</p>
								<p class="truncate text-xs text-muted" title={isQuestion(i) ? detail(i) : undefined}>{detail(i)}</p>
							</div>
						</div>
						{#if i.display === 'complete' || i.display === 'bypassed'}
							<!-- Done: the status pill replaces the action buttons; a file can still be opened and a mistake reopened through quiet icon buttons. -->
							<div class="ml-auto flex items-center gap-1.5">
								{#if canManage && i.has_document}<a class="btn btn-ghost btn-xs btn-square" href={documentHref(membership.id, i.requirement.id)} target="_blank" rel="noopener" aria-label={t('req.doc.download')} title={t('req.doc.download')}><Download size={14} /></a>{/if}
								{#if canManage && isQuestion(i) && i.display === 'complete'}<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('req.doc.q.edit')} title={t('req.doc.q.edit')} onclick={() => ((answerFor = i), (answerOpen = true))}><Pencil size={14} /></button>{/if}
								{#if canManage}
									<form method="POST" action="{actionBase}/reopenRequirement" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.reopen_confirm'))) cancel(); }}>
										<input type="hidden" name="membership_id" value={membership.id} />
										<input type="hidden" name="requirement_id" value={i.requirement.id} />
										<button class="btn btn-ghost btn-xs btn-square" type="submit" aria-label={t('req.doc.reopen')} title={t('req.doc.reopen')}><RotateCcw size={14} /></button>
									</form>
								{/if}
								<span class="badge badge-sm {l.badge}">{t(`req.doc.status.${i.display}`)}</span>
							</div>
						{:else if canManage}
							<div class="ml-auto flex flex-wrap items-center justify-end gap-1.5">
								{#if i.has_document}<a class="btn btn-outline btn-xs gap-1" href={documentHref(membership.id, i.requirement.id)} target="_blank" rel="noopener"><Download size={13} /> {t('req.doc.download')}</a>{/if}
								{#if i.display === 'pending'}
									<form method="POST" action="{actionBase}/approveRequirement" use:enhance={submit(() => {})}>
										<input type="hidden" name="membership_id" value={membership.id} />
										<input type="hidden" name="requirement_id" value={i.requirement.id} />
										<button class="btn btn-primary btn-xs gap-1" type="submit" disabled={busy}><CircleCheck size={13} /> {t('req.doc.approve')}</button>
									</form>
								{/if}
								{#if i.display !== 'missing'}
									<form method="POST" action="{actionBase}/reopenRequirement" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.reopen_confirm'))) cancel(); }}>
										<input type="hidden" name="membership_id" value={membership.id} />
										<input type="hidden" name="requirement_id" value={i.requirement.id} />
										<button class="btn btn-outline btn-error btn-xs gap-1" type="submit"><RotateCcw size={13} /> {t(i.display === 'pending' ? 'req.doc.reject' : 'req.doc.reopen')}</button>
									</form>
								{/if}
								{#if i.display === 'missing' || i.display === 'expired'}<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => ((bypassFor = i), (bypassOpen = true))}><UserCheck size={13} /> {t('req.doc.bypass')}</button>{/if}
								{#if isQuestion(i)}
									<button class="btn btn-primary btn-xs gap-1" type="button" onclick={() => ((answerFor = i), (answerOpen = true))}><Pencil size={13} /> {i.display === 'expiring_soon' ? t('req.doc.renew') : t('req.doc.q.answer')}</button>
								{:else}
									<button class="btn btn-primary btn-xs gap-1" type="button" onclick={() => ((completeFor = i), (completeOpen = true))}><Plus size={13} /> {i.display === 'expiring_soon' ? t('req.doc.renew') : t('req.doc.receive')}</button>
								{/if}
							</div>
						{:else}
							<span class="ml-auto badge badge-sm {l.badge}">{t(`req.doc.status.${i.display}`)}</span>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
		{#if canManage}<p class="mt-3 text-sm"><a class="link inline-flex items-center gap-1.5 font-semibold" href="/settings/requirements"><Settings2 size={14} aria-hidden="true" /> {t('req.doc.manage_link')}</a></p>{/if}
	{/if}
</div>

{#if membership}
	<Modal bind:open={completeOpen} title={t('req.doc.complete_title', { name: completeFor?.requirement.name ?? '' })}>
		<form method="POST" action="{actionBase}/completeRequirement" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit(() => (completeOpen = false))}>
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

	<Modal bind:open={answerOpen} title={t('req.doc.q.title', { name: answerFor?.requirement.name ?? '' })}>
		{#key answerFor?.requirement.id}
			<form method="POST" action="{actionBase}/answerRequirement" class="grid gap-3" use:enhance={submit(() => (answerOpen = false))}>
				<input type="hidden" name="membership_id" value={membership.id} />
				<input type="hidden" name="requirement_id" value={answerFor?.requirement.id ?? ''} />
				<input type="hidden" name="question_type" value={answerFor?.requirement.question_type ?? ''} />
				{#if errText && !fieldErr('answer') && !fieldErr('valid_until')}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}
				{#if answerFor?.requirement.description}<p class="text-sm text-muted">{answerFor.requirement.description}</p>{/if}
				{#if answerFor}
					<div class="form-control">
						<label class="label" for="doc-answer" id="doc-answer-label"><span class="label-text">{t('req.doc.q.your_answer')}</span></label>
						<AnswerInput question={answerFor.requirement} value={answerFor.answer} id="doc-answer" labelledby="doc-answer-label" invalid={!!fieldErr('answer')} />
						{#if fieldErr('answer')}<span class="mt-1 block text-xs text-error">{fieldErr('answer')}</span>{/if}
					</div>
				{/if}
				<div class="form-control">
					<label class="label" for="doc-q-until"><span class="label-text">{t('req.doc.valid_until')}</span></label>
					<input id="doc-q-until" name="valid_until" type="date" class="input input-bordered w-full {fieldErr('valid_until') ? 'input-error' : ''}" />
					<span class="label-text-alt mt-1 block">{answerFor?.requirement.valid_for_months ? t('req.doc.valid_until_default', { n: answerFor.requirement.valid_for_months }) : t('req.doc.valid_until_help')}</span>
					{#if fieldErr('valid_until')}<span class="mt-1 block text-xs text-error">{fieldErr('valid_until')}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="doc-q-note"><span class="label-text">{t('req.doc.note')}</span></label>
					<textarea id="doc-q-note" name="note" class="textarea textarea-bordered w-full" rows="2"></textarea>
					<span class="label-text-alt mt-1 block">{t('req.doc.q.note_help')}</span>
				</div>
				<div class="modal-action mt-2 flex justify-end gap-2">
					<button class="btn btn-outline" type="button" onclick={() => (answerOpen = false)}>{t('common.cancel')}</button>
					<button class="btn btn-primary" type="submit" disabled={busy}>{t('req.doc.save')}</button>
				</div>
			</form>
		{/key}
	</Modal>

	<Modal bind:open={bypassOpen} title={t('req.doc.bypass_title', { name: bypassFor?.requirement.name ?? '' })}>
		<form method="POST" action="{actionBase}/bypassRequirement" class="grid gap-3" use:enhance={submit(() => (bypassOpen = false))}>
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
