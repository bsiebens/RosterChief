<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { CircleCheck, FileText, ShieldCheck, TriangleAlert } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { formatDay, parseIsoDate } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { MembershipRow, RequirementItem } from '#lib/portal.ts';

	/**
	 * Per person: where the club's onboarding stands. Every requirement shows its state, the answer given or that a document was received,
	 * when it expires, and, when something is missing or runs out, the way to hand it in right here.
	 */
	let { memberships, people }: { memberships: MembershipRow[]; people: { id: string; name: string; photo_url?: string | null }[] } = $props();

	let busy = $state('');
	let errors = $state<Record<string, string>>({});
	let values = $state<Record<string, string>>({});
	let multi = $state<Record<string, string[]>>({});
	let expiry = $state<Record<string, string>>({});
	const key = (row: MembershipRow, item: RequirementItem) => `${row.member_id}:${item.id}`;
	const person = (id: string) => people.find((entry) => entry.id === id);
	const day = (value: string) => formatDay(parseIsoDate(value) ?? value);
	const needsAction = (item: RequirementItem) => ['missing', 'expired', 'expiring_soon'].includes(item.state);
	const choices = (item: RequirementItem) => (item.options ?? []).map((option) => (typeof option === 'string' ? { value: option, label: option } : option));
	const open = (row: MembershipRow) => row.requirements.filter((item) => item.state === 'missing' || item.state === 'expired').length;
	const show = (answer: unknown) => (Array.isArray(answer) ? answer.join(', ') : answer === true ? t('common.yes') : answer === false ? t('common.no') : String(answer ?? ''));
	const tone = (state: RequirementItem['state']) => (state === 'complete' || state === 'bypassed' ? 'badge-success' : state === 'expiring_soon' ? 'badge-warning' : 'badge-error');

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
			result = await api('POST', `/portal/me/requirements/${row.member_id}/${item.id}`, { form, query: { valid_until: expiry[id] } });
		}
		busy = '';
		if (result.ok) await invalidateAll();
		else errors[id] = result.message || t('common.error.generic');
	}
</script>

<section class="card" aria-labelledby="req-title">
	<div class="card-h"><ShieldCheck size={17} aria-hidden="true" /><h2 id="req-title">{t('portal.req_title')}</h2></div>
	{#each memberships as row (row.member_id + row.season)}
		{@const who = person(row.member_id)}
		<div class="border-b border-line last:border-b-0">
			<div class="flex items-center gap-3 bg-subhead px-5 py-3">
				<ClubCrest name={who?.name ?? ''} logoUrl={who?.photo_url} size={32} />
				<div class="min-w-0 flex-1"><b class="block text-ink">{who?.name}</b><span class="text-xs text-muted">{row.season}</span></div>
				{#if !row.requirements.length}<span class="badge badge-sm badge-success">{t('portal.req_none')}</span>
				{:else if open(row)}<span class="badge badge-sm badge-error">{t('portal.req_open', { n: open(row) })}</span>
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
							</div>
							<span class="badge badge-sm shrink-0 {tone(item.state)}">{t(`portal.req_state_${item.state}`)}</span>
						</div>
						{#if item.kind === 'question' && item.answer !== null && item.answer !== undefined && item.state !== 'missing'}
							<p class="rounded-box bg-subhead px-3 py-2 text-sm"><span class="text-xs text-muted">{t('portal.req_answer')}</span><br />{show(item.answer)}</p>
						{/if}
						{#if item.kind === 'document' && item.has_document}<p class="flex items-center gap-1.5 text-xs text-muted"><FileText size={13} aria-hidden="true" />{t('portal.req_received')}{#if item.completed_at} · {day(item.completed_at)}{/if}</p>{/if}
						{#if item.valid_until}
							<p class="flex items-center gap-1.5 text-xs {item.state === 'expired' ? 'font-semibold text-danger' : item.state === 'expiring_soon' ? 'font-semibold text-warn-text' : 'text-muted'}">{#if item.state !== 'complete'}<TriangleAlert size={13} aria-hidden="true" />{/if}{t(item.state === 'expired' ? 'portal.req_expired_on' : 'portal.req_valid_until', { date: day(item.valid_until) })}</p>
						{/if}
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
									<button class="btn btn-primary btn-sm w-fit" type="submit" disabled={busy === id}>{t('portal.req_send')}</button>
								{:else}
									<label class="grid gap-1 text-sm font-semibold">{t('portal.req_file')}
										<input class="input w-full font-normal" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" disabled={busy === id} onchange={(e) => { const file = e.currentTarget.files?.[0]; if (file) void submit(row, item, file); e.currentTarget.value = ''; }} />
									</label>
									<label class="grid gap-1 text-sm font-semibold sm:max-w-56">{t('portal.req_expiry')}<input class="input w-full font-normal" type="date" bind:value={expiry[id]} /></label>
									<p class="text-xs text-muted">{t('portal.req_expiry_help')}</p>
								{/if}
								{#if errors[id]}<Alert kind="error">{errors[id]}</Alert>{/if}
							</form>
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
