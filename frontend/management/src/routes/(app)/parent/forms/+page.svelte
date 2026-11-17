<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { ChevronRight, CircleCheck, ClipboardList, MessageSquareQuote, Star } from '@lucide/svelte';
	import FormResponse from '#lib/components/forms/FormResponse.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { answerText } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PublicForm } from '#lib/form-response.ts';
	import { api } from '#lib/management-api.ts';
	import type { SharedEvaluation } from '#lib/portal.ts';

	let { data } = $props();
	let selected = $state('');
	const people = $derived(data.home.people);
	usePageHeader(() => ({ title: t('portal.forms_evaluations') }));
	const person = (id: string) => people.find((candidate) => candidate.id === id);
	const forms = $derived(data.home.forms.filter((form) => !selected || form.member_id === selected));
	const submitted = $derived((data.home.submitted_forms ?? []).filter((form) => !selected || form.member_id === selected));
	const evaluations = $derived(data.evaluations.filter((evaluation) => !selected || evaluation.member_id === selected));
	let opened = $state<SharedEvaluation | null>(null);

	// The form being filled in opens in the drawer; `?fill=<send>.<member>` (old links, notifications) opens it straight away.
	type ParentForm = PublicForm & { member: string; submitted: number; limit: number | null };
	let filling = $state<{ endpoint: string; form: ParentForm | null; title: string; failed: boolean } | null>(null);
	async function fill(send: string, member: string) {
		const endpoint = `/portal/forms/${encodeURIComponent(send)}/${encodeURIComponent(member)}`;
		const title = data.home.forms.find((form) => form.id === send && form.member_id === member)?.title ?? t('portal.forms');
		filling = { endpoint, form: null, title, failed: false };
		const result = await api<ParentForm>('GET', endpoint);
		if (filling?.endpoint === endpoint) filling = { endpoint, form: result.ok ? (result.data ?? null) : null, title: result.data?.title ?? title, failed: !result.ok };
	}
	const closeForm = () => {
		filling = null;
		if (page.url.searchParams.has('fill')) void goto('/parent/forms', { replaceState: true });
	};
	// Done: let the thank-you show for a moment, then close and refresh the list.
	function filled() {
		setTimeout(async () => {
			closeForm();
			await invalidateAll();
		}, 1400);
	}
	onMount(() => {
		const [send, member] = (page.url.searchParams.get('fill') ?? '').split('.');
		if (send && member) void fill(send, member);
	});
	const shown = (answer: SharedEvaluation['answers'][number]) => answerText({ field_type: answer.field_type, options: answer.options } as never, answer.value, t('common.yes'), t('common.no'));
	const scores = (evaluation: SharedEvaluation) => evaluation.answers.filter((answer) => answer.field_type !== 'text' && answer.field_type !== 'textarea' && shown(answer));
	const comments = (evaluation: SharedEvaluation) => evaluation.answers.filter((answer) => (answer.field_type === 'text' || answer.field_type === 'textarea') && shown(answer));
</script>

<div class="grid min-w-0 gap-4">
	<div class="hidden md:block"><PersonFilter {people} bind:selected viewer={data.home.viewer} /></div>
	<div class="grid min-w-0 items-start gap-4 {data.evaluations.length ? 'xl:grid-cols-2' : 'max-w-3xl'}">
		<section class="card" aria-labelledby="forms-title">
			<div class="card-h"><ClipboardList size={17} aria-hidden="true" /><h2 id="forms-title">{t('portal.forms_open')}</h2>{#if forms.length}<span class="badge badge-sm badge-error ml-auto">{t('portal.forms_to_fill', { n: forms.length })}</span>{/if}</div>
			<div class="divide-y divide-rule">
				{#each forms as form (`${form.id}-${form.member_id}`)}
					<div class="flex min-h-16 items-center gap-3 px-5 py-3">
						<ClubCrest name={person(form.member_id)?.name ?? ''} logoUrl={person(form.member_id)?.photo_url} size={36} />
						<span class="min-w-0 flex-1"><b class="block text-ink">{form.title}</b><span class="block text-xs text-muted">{person(form.member_id)?.name}{form.closes_at ? ` · ${t('pubform.closes', { date: formatDateTime(form.closes_at) })}` : ''}</span></span>
						<button type="button" class="btn btn-primary btn-sm" onclick={() => fill(form.id, form.member_id)}>{t('portal.form_fill')}</button>
					</div>
				{:else}
					<p class="flex items-center gap-2 px-5 py-4 text-sm text-muted"><CircleCheck size={16} class="text-ok" aria-hidden="true" />{t('portal.no_forms')}</p>
				{/each}
			</div>
		</section>

		{#if submitted.length}
			<section class="card" aria-labelledby="done-title">
				<div class="card-h"><CircleCheck size={17} aria-hidden="true" /><h2 id="done-title">{t('portal.forms_done')}</h2></div>
				<div class="divide-y divide-rule">
					{#each submitted as form (form.id)}
						<div class="flex min-h-14 items-center gap-3 px-5 py-2"><CircleCheck size={18} class="shrink-0 text-ok" aria-hidden="true" /><span class="min-w-0 flex-1"><b class="block truncate text-ink">{form.title}</b><span class="block text-xs text-muted">{person(form.member_id)?.name} · {formatDateTime(form.submitted_at)}</span></span></div>
					{/each}
				</div>
			</section>
		{/if}

		{#if evaluations.length}
			<section class="card" aria-labelledby="evals-title">
				<div class="card-h"><Star size={17} aria-hidden="true" /><h2 id="evals-title">{t('portal.evals_title')}</h2></div>
				<div class="divide-y divide-rule">
					{#each evaluations as evaluation (evaluation.id)}
						<button type="button" class="flex min-h-16 w-full items-center gap-3 px-5 py-3 text-left hover:bg-subhead" onclick={() => (opened = evaluation)}>
							<ClubCrest name={person(evaluation.member_id)?.name ?? ''} logoUrl={person(evaluation.member_id)?.photo_url} size={36} />
							<span class="min-w-0 flex-1"><b class="block text-ink">{evaluation.checklist}</b><span class="block text-xs text-muted">{person(evaluation.member_id)?.name} · {evaluation.season}{evaluation.released_at ? ` · ${formatDateTime(evaluation.released_at)}` : ''}</span>
								{#if evaluation.public_note}<span class="mt-1 line-clamp-2 block text-sm text-slate">{evaluation.public_note}</span>{/if}</span>
							<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
						</button>
					{/each}
				</div>
			</section>
		{/if}
	</div>
</div>

<Sheet open={!!filling} wide title={filling?.title ?? t('portal.forms')} onclose={closeForm}>
	{#if filling}
		<div class="grid gap-3 p-4 pt-0">
			<h2 class="font-display text-xl font-extrabold text-ink uppercase">{filling.title}</h2>
			{#if filling.form}
				<p class="text-sm font-semibold">{filling.form.member}</p>
				{#if filling.form.limit !== null && filling.form.submitted >= filling.form.limit}
					<p class="flex items-center gap-2 text-sm text-muted"><CircleCheck size={16} class="text-ok" aria-hidden="true" />{t('pubform.thanks.title')}</p>
				{:else}
					<FormResponse form={filling.form} endpoint={filling.endpoint} ondone={filled} />
				{/if}
			{:else if filling.failed}
				<p class="text-sm text-error" role="alert">{t('common.error.generic')}</p>
			{:else}
				<p class="text-sm text-muted">{t('common.loading')}</p>
			{/if}
		</div>
	{/if}
</Sheet>

<Sheet open={!!opened} title={opened?.checklist ?? t('portal.evals_title')} onclose={() => (opened = null)}>
	{#if opened}
		<div class="grid gap-4 p-4 pt-0">
			<div>
				<h2 class="font-display text-xl font-extrabold text-ink uppercase">{opened.checklist}</h2>
				<p class="text-xs text-muted">{person(opened.member_id)?.name} · {opened.season}</p>
			</div>
			{#if opened.public_note}
				<div class="rounded-box border border-line bg-subhead p-4">
					<p class="mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted"><MessageSquareQuote size={14} aria-hidden="true" />{t('portal.evals_note')}</p>
					<p class="text-sm whitespace-pre-line text-ink">{opened.public_note}</p>
				</div>
			{/if}
			{#if opened.release === 'full'}
				{#if scores(opened).length}
					<dl class="divide-y divide-rule rounded-box border border-line">
						{#each scores(opened) as answer, i (i)}
							<div class="flex items-center justify-between gap-3 px-4 py-2.5"><dt class="text-sm text-muted">{answer.label}</dt><dd class="font-mono text-sm font-semibold text-ink">{shown(answer)}</dd></div>
						{/each}
					</dl>
				{/if}
				{#each comments(opened) as answer, i (i)}
					<div class="rounded-box border border-line p-4"><p class="mb-1 text-xs font-semibold text-muted">{answer.label}</p><p class="text-sm whitespace-pre-line text-ink">{shown(answer)}</p></div>
				{/each}
			{/if}
		</div>
	{/if}
</Sheet>
