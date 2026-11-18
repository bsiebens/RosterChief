<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { CircleCheck, Download, FileText, ShieldCheck, TriangleAlert, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { formatDay, parseIsoDate } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { sentFileHref, type MembershipRow, type RequirementItem } from '#lib/portal.ts';

	/**
	 * Per person: where the club's onboarding stands. Every requirement shows its state, the answer given or that a document was received,
	 * when it expires, and, when something is missing or runs out, the way to hand it in right here.
	 */
	let { memberships, people }: { memberships: MembershipRow[]; people: { id: string; name: string; photo_url?: string | null }[] } = $props();

	let busy = $state('');
	let errors = $state<Record<string, string>>({});
	let values = $state<Record<string, string>>({});
	let multi = $state<Record<string, string[]>>({});
	const key = (row: MembershipRow, item: RequirementItem) => `${row.member_id}:${item.id}`;
	const person = (id: string) => people.find((entry) => entry.id === id);
	const day = (value: string) => formatDay(parseIsoDate(value) ?? value);
	const needsAction = (item: RequirementItem) => !item.locked && !item.awaiting_file;
	const choices = (item: RequirementItem) => (item.options ?? []).map((option) => (typeof option === 'string' ? { value: option, label: option } : option));
	const open = (row: MembershipRow) => row.requirements.filter((item) => item.state === 'missing' || item.state === 'expired' || item.state === 'declined' || item.state === 'requested').length;
	const waiting = (row: MembershipRow) => row.requirements.filter((item) => item.state === 'pending').length;
	const show = (answer: unknown) => (Array.isArray(answer) ? answer.join(', ') : answer === true ? t('common.yes') : answer === false ? t('common.no') : String(answer ?? ''));
	const tone = (state: RequirementItem['state']) => (state === 'complete' || state === 'bypassed' ? 'badge-success' : state === 'pending' ? 'badge-info' : state === 'expiring_soon' || state === 'requested' ? 'badge-warning' : 'badge-error');

	async function submit(row: MembershipRow, item: RequirementItem, file?: File | null) {
		const id = key(row, item);
		busy = id;
		errors[id] = '';
		let result;
		if (item.kind === 'question') {
			const answer = item.question_type === 'multichoice' ? JSON.stringify(multi[id] ?? []) : (values[id] ?? '');
			result = await api('POST', `/portal/me/requirements/${row.member_id}/${item.id}`, { query: { answer } });
		} else {
			const form = new FormData();
			if (file) form.append('document', file);
			result = await api('POST', `/portal/me/requirements/${row.member_id}/${item.id}`, { form });
		}
		busy = '';
		if (result.ok) await invalidateAll();
		else errors[id] = result.message || t('common.error.generic');
	}
</script>

<section class="card req-card" aria-labelledby="req-title">
	<div class="card-h max-md:hidden"><ShieldCheck size={17} aria-hidden="true" /><h2 id="req-title">{t('portal.req_title')}</h2></div>
	{#each memberships as row (row.member_id + row.season)}
		{@const who = person(row.member_id)}
		<div class="border-b border-line last:border-b-0 req-member">
			<div class="flex items-center gap-3 bg-subhead px-5 py-3">
				<ClubCrest name={who?.name ?? ''} logoUrl={who?.photo_url} size={32} />
				<div class="min-w-0 flex-1"><b class="block text-ink">{who?.name}</b><span class="text-xs text-muted">{row.season}</span></div>
				{#if !row.requirements.length}<span class="badge badge-sm badge-success">{t('portal.req_none')}</span>
				{:else if open(row)}<span class="badge badge-sm badge-error">{t('portal.req_open', { n: open(row) })}</span>
				{:else if waiting(row)}<span class="badge badge-sm badge-info">{t('portal.req_waiting', { n: waiting(row) })}</span>
				{:else}<span class="badge badge-sm badge-success"><CircleCheck size={12} aria-hidden="true" />{t('portal.req_done')}</span>{/if}
			</div>
			<ul class="divide-y divide-rule">
				{#each row.requirements as item (item.id)}
					{@const id = key(row, item)}
					<li class="grid gap-2 px-5 py-3">
						<div class="flex items-start gap-3">
							<div class="min-w-0 flex-1">
								<p class="font-semibold text-ink">{item.name}</p>
								{#if item.description}<p class="text-xs text-muted">{item.description}</p>{/if}
								{#if item.member_instructions}<p class="mt-1.5 rounded-box bg-subhead px-3 py-2 text-sm text-ink"><span class="text-xs font-semibold text-muted">{t('portal.req_instructions')}</span><br />{item.member_instructions}</p>{/if}
							</div>
							<span class="badge badge-sm shrink-0 {tone(item.state)}">{t(`portal.req_state_${item.state}`)}</span>
						</div>
						{#if item.kind === 'question' && item.answer !== null && item.answer !== undefined && item.state !== 'missing'}
							<p class="rounded-box bg-subhead px-3 py-2 text-sm"><span class="text-xs text-muted">{t('portal.req_answer')}</span><br />{show(item.answer)}</p>
						{/if}
						{#if item.review_note && (item.state === 'declined' || item.state === 'requested')}
							<p class="rounded-box px-3 py-2 text-sm {item.state === 'declined' ? 'bg-danger-bg' : 'bg-warn-bg'}"><span class="text-xs font-semibold text-muted">{t(item.state === 'declined' ? 'portal.req_reason' : 'portal.req_requested_label')}</span><br />{item.review_note}</p>
							{#if item.state === 'declined'}<p class="text-xs text-muted">{t('portal.req_declined_help')}</p>{/if}
						{/if}
						{#if item.has_sent_file && !item.locked}
							<div class="grid gap-1.5 rounded-box border border-line px-3 py-2.5">
								<p class="text-sm font-semibold text-ink">{t('portal.req_sent_title')}</p>
								{#if item.sent_note}<p class="text-sm text-ink">{item.sent_note}</p>{/if}
								<p class="text-xs text-muted">{t('portal.req_sent_help')}</p>
								<a class="btn btn-outline btn-sm w-fit gap-1.5" href={sentFileHref(row.member_id, item.id)} target="_blank" rel="noopener"><Download size={14} aria-hidden="true" />{t('portal.req_sent_download')}</a>
							</div>
						{/if}
						{#if item.kind === 'document' && item.has_document && item.state !== 'declined'}<p class="flex items-center gap-1.5 text-xs text-muted"><FileText size={13} aria-hidden="true" />{t('portal.req_received')}{#if item.completed_at}{' · '}{day(item.completed_at)}{/if}</p>{/if}
						{#if item.valid_until}
							<p class="flex items-center gap-1.5 text-xs {item.state === 'expired' ? 'font-semibold text-danger' : item.state === 'expiring_soon' ? 'font-semibold text-warn-text' : 'text-muted'}">{#if item.state !== 'complete'}<TriangleAlert size={13} aria-hidden="true" />{/if}{t(item.state === 'expired' ? 'portal.req_expired_on' : 'portal.req_valid_until', { date: day(item.valid_until) })}</p>
						{/if}
						{#if item.awaiting_file && !item.locked}<p class="rounded-box bg-warn-bg px-3 py-2 text-sm text-warn-text">{t('portal.req_awaiting')}</p>{/if}
						{#if item.state === 'pending'}<p class="text-xs text-info-text">{t('portal.req_pending_help')}</p>{/if}
						{#if item.state === 'expiring_soon'}<p class="text-xs text-muted">{t('portal.req_renew_help')}</p>{/if}
						{#if needsAction(item)}
							<form class="grid gap-2 rounded-box border border-line p-3" onsubmit={(e) => { e.preventDefault(); if (item.kind === 'question') void submit(row, item); }}>
								{#if item.kind === 'question'}
									{#if item.question_type === 'yes_no'}
										<select class="select w-full" bind:value={values[id]} required><option value="" disabled>{t('portal.req_choose')}</option><option value="yes">{t('common.yes')}</option><option value="no">{t('common.no')}</option></select>
									{:else if item.question_type === 'choice'}
										<select class="select w-full" bind:value={values[id]} required><option value="" disabled>{t('portal.req_choose')}</option>{#each choices(item) as option (option.value)}<option value={option.value}>{option.label}</option>{/each}</select>
									{:else if item.question_type === 'multichoice'}
										<div class="grid gap-1">{#each choices(item) as option (option.value)}<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox" value={option.value} bind:group={multi[id]} />{option.label}</label>{/each}</div>
									{:else if item.question_type === 'textarea'}
										<textarea class="textarea w-full" rows="3" bind:value={values[id]} required></textarea>
									{:else}
										<input class="input w-full" type={item.question_type === 'number' ? 'number' : item.question_type === 'date' ? 'date' : 'text'} bind:value={values[id]} required />
									{/if}
									<button class="btn btn-primary btn-sm w-fit" type="submit" disabled={busy === id}>{item.state === 'pending' ? t('portal.req_change') : t('portal.req_send')}</button>
								{:else}
									<label class="flex min-h-16 cursor-pointer items-center justify-center gap-2 rounded-box border-2 border-dashed border-club bg-club/10 px-4 py-3 text-sm font-semibold text-club hover:bg-club/20 focus-within:ring-2 focus-within:ring-club {busy === id ? 'pointer-events-none opacity-60' : ''}">
										<Upload size={18} aria-hidden="true" />{t('portal.req_file')}
										<input class="sr-only" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" disabled={busy === id} onchange={(e) => { const file = e.currentTarget.files?.[0]; if (file) void submit(row, item, file); e.currentTarget.value = ''; }} />
									</label>
									<p class="text-xs text-muted">{t('portal.req_expiry_help')}</p>
								{/if}
								{#if errors[id]}<Alert kind="error">{errors[id]}</Alert>{/if}
							</form>
						{/if}
						{#if item.history.length}
							<details class="text-xs">
								<summary class="cursor-pointer font-semibold text-muted">{t('portal.req_history')}</summary>
								<ul class="mt-1.5 grid gap-1.5 border-l-2 border-rule pl-3">
									{#each item.history as event (event.created + event.kind)}
										<li class="min-w-0"><span class="font-semibold text-ink">{t(`portal.req_h_${event.kind}`)}</span> <span class="text-muted">· {day(event.created)}</span>{#if event.note}<p class="break-words text-ink">{event.note}</p>{/if}</li>
									{/each}
								</ul>
							</details>
						{/if}
					</li>
				{:else}
					<li class="px-5 py-3 text-sm text-muted">{t('portal.req_none')}</li>
				{/each}
			</ul>
		</div>
	{:else}
		<p class="px-5 py-4 text-sm text-muted">{t('portal.req_no_membership')}</p>
	{/each}
</section>
