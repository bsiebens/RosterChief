<script lang="ts">
	import { enhance } from '$app/forms';
	import { CircleAlert, CircleCheck, CircleQuestionMark, CircleX, Clock, Download, FileText, FileX, Hourglass, MessageSquareText, Paperclip, Pencil, Plus, RotateCcw, Send, Settings2, Trash2, UserCheck } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import AnswerInput from '#lib/components/requirements/AnswerInput.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { answerText, documentHref, sentFileHref, type Checklist, type ChecklistItem, type DocDisplay } from '#lib/requirements.ts';

	/**
	 * A member's documents and questions for one season, from the requirements the club defined (a question shows its answer; staff can answer it). Everyone who may see the member sees the status;
	 * marking received (with a file and an expiry date), "not needed", reopening and downloading need `members.manage_members`.
	 * `form` is the page's action result, for the errors of the dialogs.
	 */
	let { checklist, canManage = false, form, actionBase = '?tab=documents&', single = false }: { checklist: Checklist | null; canManage?: boolean; /** One column: for a narrow place such as a dialog. */ single?: boolean; form: any; /** Where the form actions live: `?tab=documents&` on the member page, `?` on a page that spreads `requirementActions`. */ actionBase?: string } = $props();
	const items = $derived(checklist?.items ?? []);
	const membership = $derived(checklist?.membership ?? null);

	const look: Record<DocDisplay, { badge: string; tint: string; icon: typeof CircleCheck }> = {
		complete: { badge: 'badge-success', tint: 'text-ok-text', icon: CircleCheck },
		bypassed: { badge: 'badge-ghost', tint: 'text-muted', icon: UserCheck },
		expiring_soon: { badge: 'badge-warning', tint: 'text-warn-text', icon: Clock },
		expired: { badge: 'badge-error', tint: 'text-danger', icon: CircleAlert },
		missing: { badge: 'badge-ghost', tint: 'text-muted', icon: FileX },
		pending: { badge: 'badge-info', tint: 'text-info-text', icon: Hourglass },
		declined: { badge: 'badge-error', tint: 'text-danger', icon: CircleX },
		requested: { badge: 'badge-warning', tint: 'text-warn-text', icon: MessageSquareText }
	};
	const isQuestion = (i: ChecklistItem) => i.requirement.kind === 'question';
	const shownAnswer = (i: ChecklistItem) => answerText(i.requirement, i.answer, t('common.yes'), t('common.no'), formatDate);
	const detail = (i: ChecklistItem) => {
		if (i.display === 'declined') return t('req.doc.d.declined');
		if (i.display === 'requested') return t('req.doc.d.requested');
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

	const od = $derived(checklist?.overdue ?? null);
	const odKinds = (kinds: string[]) => kinds.map((k) => t(`eventform.kind.${k}`)).join(', ');
	const money = (v: string) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'EUR' }).format(Number(v));

	let completeFor = $state<ChecklistItem | null>(null);
	let completeOpen = $state(false);
	let bypassFor = $state<ChecklistItem | null>(null);
	let bypassOpen = $state(false);
	let answerFor = $state<ChecklistItem | null>(null);
	let answerOpen = $state(false);
	// Decline and Request both take a message the member sees; one dialog serves both.
	let noteFor = $state<ChecklistItem | null>(null);
	let noteAction = $state<'decline' | 'request'>('decline');
	let noteOpen = $state(false);
	let sendFor = $state<ChecklistItem | null>(null);
	let sendOpen = $state(false);
	let busy = $state(false);
	const askNote = (i: ChecklistItem, action: 'decline' | 'request') => ((noteFor = i), (noteAction = action), (noteOpen = true));
	const stamp = (iso: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
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
	{#if errText && !completeOpen && !bypassOpen && !answerOpen && !noteOpen && !sendOpen}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}

	{#if od}
		<div class="mt-3 flex items-start gap-3 rounded-lg border border-error bg-danger-bg/40 p-3.5" role="alert">
			<span class="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface text-danger"><CircleAlert size={18} aria-hidden="true" /></span>
			<div class="min-w-0 flex-1">
				<p class="font-semibold text-ink">{t('req.doc.overdue_title')}</p>
				<p class="text-xs text-muted">{t('req.doc.overdue_body', { amount: money(od.remaining), date: formatDate(od.due_date) })}</p>
				<p class="mt-1 text-xs {od.blocking ? 'font-semibold text-danger' : 'text-muted'}">{od.block_kinds.length ? t(od.blocking ? 'req.doc.overdue_blocking' : 'req.doc.overdue_grace', { date: formatDate(od.grace_until), kinds: odKinds(od.block_kinds) }) : t('req.doc.overdue_no_block')}</p>
			</div>
		</div>
	{/if}
	{#if !membership}
		<p class="mt-3 text-sm text-muted">{t('req.doc.no_membership')}</p>
	{:else if !items.length}
		<p class="mt-3 text-sm text-muted">{canManage ? t('req.doc.none_manage') : t('req.doc.none')} {#if canManage}<a class="link" href="/settings/requirements">{t('req.doc.manage_link')}</a>{/if}</p>
	{:else}
		<ul class="mt-3 grid gap-3 {single ? '' : 'md:grid-cols-2 xl:grid-cols-3'}">
			{#each items as i (i.requirement.id)}
				{@const l = look[i.display]}
				{@const waiting = i.awaiting_file}
				<li class="rounded-lg border p-3.5 {waiting ? 'border-warn-border bg-warn-bg/40' : 'border-line'}">
					<div class="flex items-start gap-3">
						<span class="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface {l.tint}">
							{#if isQuestion(i) && i.display === 'missing'}<CircleQuestionMark size={18} />{:else}<l.icon size={18} />{/if}
						</span>
						<div class="min-w-0 flex-1">
							<p class="font-semibold text-ink" title={i.requirement.description}>{i.requirement.name}</p>
							<p class="text-xs text-muted">{detail(i)}</p>
						</div>
						<div class="flex shrink-0 items-center gap-1">
							{#if canManage && (i.display === 'complete' || i.display === 'bypassed')}
								{#if i.has_document}<a class="btn btn-ghost btn-xs btn-square" href={documentHref(membership.id, i.requirement.id)} target="_blank" rel="noopener" aria-label={t('req.doc.download')} title={t('req.doc.download')}><Download size={14} /></a>{/if}
								{#if isQuestion(i) && i.display === 'complete'}<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('req.doc.q.edit')} title={t('req.doc.q.edit')} onclick={() => ((answerFor = i), (answerOpen = true))}><Pencil size={14} /></button>{/if}
								{#if i.display === 'complete'}<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('req.doc.request')} title={t('req.doc.request_tip')} onclick={() => askNote(i, 'request')}><MessageSquareText size={14} /></button>{/if}
								<form method="POST" action="{actionBase}/reopenRequirement" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.reopen_confirm'))) cancel(); }}>
									<input type="hidden" name="membership_id" value={membership.id} />
									<input type="hidden" name="requirement_id" value={i.requirement.id} />
									<button class="btn btn-ghost btn-xs btn-square" type="submit" aria-label={t('req.doc.reopen')} title={t('req.doc.reopen')}><RotateCcw size={14} /></button>
								</form>
							{/if}
							<span class="badge badge-sm {l.badge}">{t(`req.doc.status.${i.display}`)}</span>
						</div>
					</div>
					{#if i.requirement.member_instructions}
						<p class="mt-2.5 rounded-md bg-surface px-3 py-2 text-xs text-ink"><span class="font-semibold">{t('req.doc.instructions')}</span> {i.requirement.member_instructions}</p>
					{/if}
					{#if waiting}
						<p class="mt-2.5 flex items-start gap-2 text-xs font-semibold text-warn-text"><Paperclip size={13} class="mt-0.5 shrink-0" aria-hidden="true" />{canManage ? t('req.doc.awaiting_staff') : t('req.doc.awaiting_member')}</p>
					{/if}
					{#if canManage && i.display !== 'complete' && i.display !== 'bypassed'}
						<div class="mt-3 flex flex-wrap items-center gap-1.5 border-t border-rule pt-3">
							{#if isQuestion(i)}
								<button class="btn btn-primary btn-xs gap-1" type="button" onclick={() => ((answerFor = i), (answerOpen = true))}><Pencil size={13} /> {i.display === 'expiring_soon' ? t('req.doc.renew') : t('req.doc.q.answer')}</button>
							{:else if waiting}
								<button class="btn btn-primary btn-xs gap-1" type="button" onclick={() => ((sendFor = i), (sendOpen = true))}><Send size={13} /> {t('req.doc.send_first')}</button>
								<button class="btn btn-outline btn-xs gap-1" type="button" title={t('req.doc.receive_tip')} onclick={() => ((completeFor = i), (completeOpen = true))}><Plus size={13} /> {t('req.doc.receive')}</button>
							{:else}
								<button class="btn btn-primary btn-xs gap-1" type="button" title={t('req.doc.receive_tip')} onclick={() => ((completeFor = i), (completeOpen = true))}><Plus size={13} /> {i.display === 'expiring_soon' ? t('req.doc.renew') : t('req.doc.receive')}</button>
							{/if}
							{#if i.display === 'pending'}
								<form method="POST" action="{actionBase}/approveRequirement" use:enhance={submit(() => {})}>
									<input type="hidden" name="membership_id" value={membership.id} />
									<input type="hidden" name="requirement_id" value={i.requirement.id} />
									<button class="btn btn-primary btn-xs gap-1" type="submit" disabled={busy}><CircleCheck size={13} /> {t('req.doc.approve')}</button>
								</form>
								<button class="btn btn-outline btn-error btn-xs gap-1" type="button" onclick={() => askNote(i, 'decline')}><CircleX size={13} /> {t('req.doc.decline')}</button>
							{/if}
							{#if i.has_document}<a class="btn btn-outline btn-xs gap-1" href={documentHref(membership.id, i.requirement.id)} target="_blank" rel="noopener"><Download size={13} /> {t('req.doc.download')}</a>{/if}
							{#if !isQuestion(i) && !waiting}<button class="btn btn-outline btn-xs gap-1" type="button" title={t('req.doc.send_tip')} onclick={() => ((sendFor = i), (sendOpen = true))}><Send size={13} /> {t(i.has_sent_file ? 'req.doc.send_replace' : 'req.doc.send')}</button>{/if}
							{#if i.display !== 'requested' && i.display !== 'declined'}<button class="btn btn-outline btn-xs gap-1" type="button" title={t('req.doc.request_tip')} onclick={() => askNote(i, 'request')}><MessageSquareText size={13} /> {t('req.doc.request')}</button>{/if}
							{#if i.display === 'missing' || i.display === 'expired' || i.display === 'declined' || i.display === 'requested'}<button class="btn btn-ghost btn-xs gap-1" type="button" title={t('req.doc.bypass_tip')} onclick={() => ((bypassFor = i), (bypassOpen = true))}><UserCheck size={13} /> {t('req.doc.bypass')}</button>{/if}
							{#if i.display !== 'missing'}
								<form method="POST" action="{actionBase}/reopenRequirement" class="ml-auto" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.reopen_confirm'))) cancel(); }}>
									<input type="hidden" name="membership_id" value={membership.id} />
									<input type="hidden" name="requirement_id" value={i.requirement.id} />
									<button class="btn btn-ghost btn-xs gap-1 text-danger" type="submit"><RotateCcw size={13} /> {t('req.doc.reopen')}</button>
								</form>
							{/if}
						</div>
					{/if}
					{#if i.review_note && (i.display === 'declined' || i.display === 'requested')}
						<p class="mt-2.5 rounded-md bg-surface px-3 py-2 text-xs text-ink"><span class="font-semibold">{t(i.display === 'declined' ? 'req.doc.note_declined' : 'req.doc.note_requested')}</span> {i.review_note}</p>
					{/if}
					{#if i.has_sent_file}
						<div class="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted">
							<Paperclip size={13} aria-hidden="true" />
							<span class="min-w-0">{t('req.doc.sent_label', { date: i.sent_at ? stamp(i.sent_at) : '' })}{#if i.sent_note} · {i.sent_note}{/if}</span>
							{#if canManage}
								<a class="link font-semibold" href={sentFileHref(membership.id, i.requirement.id)} target="_blank" rel="noopener">{t('req.doc.download')}</a>
								{#if i.display !== 'complete'}
									<form method="POST" action="{actionBase}/removeSentFile" use:enhance={({ cancel }) => { if (!confirm(t('req.doc.sent_remove_confirm'))) cancel(); }}>
										<input type="hidden" name="membership_id" value={membership.id} />
										<input type="hidden" name="requirement_id" value={i.requirement.id} />
										<button class="inline-flex items-center gap-1 font-semibold text-danger" type="submit"><Trash2 size={12} aria-hidden="true" /> {t('req.doc.sent_remove')}</button>
									</form>
								{/if}
							{/if}
						</div>
					{/if}
					{#if canManage && i.history.length}
						<details class="mt-2.5 text-xs">
							<summary class="cursor-pointer font-semibold text-muted">{t('req.doc.history', { n: i.history.length })}</summary>
							<ol class="mt-1.5 grid gap-1.5 border-l-2 border-rule pl-3">
								{#each i.history as e (e.id)}
									<li class="min-w-0">
										<span class="font-semibold text-ink">{t(`req.doc.h.${e.kind}`)}</span>
										<span class="text-muted">· {stamp(e.created)}{#if e.actor_name} · {e.actor_name}{/if}</span>
										{#if e.note}<p class="break-words text-ink">{e.note}</p>{/if}
									</li>
								{/each}
							</ol>
						</details>
					{/if}
				</li>
			{/each}
		</ul>
		{#if canManage}
			<details class="mt-3 rounded-lg bg-surface px-3 py-2 text-xs text-muted">
				<summary class="cursor-pointer font-semibold text-ink">{t('req.doc.legend')}</summary>
				<dl class="mt-2 grid gap-1.5">
					<div><dt class="inline font-semibold text-ink">{t('req.doc.receive')}:</dt> <dd class="inline">{t('req.doc.receive_tip')}</dd></div>
					<div><dt class="inline font-semibold text-ink">{t('req.doc.send')}:</dt> <dd class="inline">{t('req.doc.send_tip')}</dd></div>
					<div><dt class="inline font-semibold text-ink">{t('req.doc.request')}:</dt> <dd class="inline">{t('req.doc.request_tip')}</dd></div>
					<div><dt class="inline font-semibold text-ink">{t('req.doc.bypass')}:</dt> <dd class="inline">{t('req.doc.bypass_tip')}</dd></div>
				</dl>
			</details>
		{/if}
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

	<Modal bind:open={noteOpen} title={t(noteAction === 'decline' ? 'req.doc.decline_title' : 'req.doc.request_title', { name: noteFor?.requirement.name ?? '' })}>
		{#key `${noteAction}:${noteFor?.requirement.id}`}
			<form method="POST" action="{actionBase}/{noteAction}Requirement" class="grid gap-3" use:enhance={submit(() => (noteOpen = false))}>
				<input type="hidden" name="membership_id" value={membership.id} />
				<input type="hidden" name="requirement_id" value={noteFor?.requirement.id ?? ''} />
				{#if errText && !fieldErr('note')}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}
				<div class="form-control">
					<label class="label" for="doc-review-note"><span class="label-text">{t(noteAction === 'decline' ? 'req.doc.decline_reason' : 'req.doc.request_message')} <span class="text-error" aria-hidden="true">*</span></span></label>
					<textarea id="doc-review-note" name="note" class="textarea textarea-bordered w-full {fieldErr('note') ? 'textarea-error' : ''}" rows="3" required></textarea>
					<span class="label-text-alt mt-1 block">{t(noteAction === 'decline' ? 'req.doc.decline_help' : 'req.doc.request_help')}</span>
					{#if fieldErr('note')}<span class="mt-1 block text-xs text-error">{fieldErr('note')}</span>{/if}
				</div>
				<div class="modal-action mt-2 flex justify-end gap-2">
					<button class="btn btn-outline" type="button" onclick={() => (noteOpen = false)}>{t('common.cancel')}</button>
					<button class="btn {noteAction === 'decline' ? 'btn-error' : 'btn-primary'}" type="submit" disabled={busy}>{t(noteAction === 'decline' ? 'req.doc.decline_send' : 'req.doc.request_send')}</button>
				</div>
			</form>
		{/key}
	</Modal>

	<Modal bind:open={sendOpen} title={t('req.doc.send_title', { name: sendFor?.requirement.name ?? '' })}>
		<form method="POST" action="{actionBase}/sendRequirementFile" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit(() => (sendOpen = false))}>
			<input type="hidden" name="membership_id" value={membership.id} />
			<input type="hidden" name="requirement_id" value={sendFor?.requirement.id ?? ''} />
			{#if errText && !fieldErr('document')}<Alert kind="error" title={t('req.doc.failed')}>{errText}</Alert>{/if}
			<p class="text-sm text-muted">{t('req.doc.send_help')}</p>
			<div class="form-control">
				<label class="label" for="doc-send-file"><span class="label-text">{t('req.doc.file')} <span class="text-error" aria-hidden="true">*</span></span></label>
				<input id="doc-send-file" name="document" type="file" required class="file-input file-input-bordered w-full {fieldErr('document') ? 'file-input-error' : ''}" />
				{#if sendFor?.has_sent_file}<span class="label-text-alt mt-1 block">{t('req.doc.send_replaces')}</span>{/if}
				{#if fieldErr('document')}<span class="mt-1 block text-xs text-error">{fieldErr('document')}</span>{/if}
			</div>
			<div class="form-control">
				<label class="label" for="doc-send-note"><span class="label-text">{t('req.doc.send_note')}</span></label>
				<textarea id="doc-send-note" name="note" class="textarea textarea-bordered w-full" rows="2"></textarea>
			</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (sendOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={busy}>{t('req.doc.send_go')}</button>
			</div>
		</form>
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
