<script lang="ts">
	import { ChevronRight, CircleCheck, ClipboardList, MessageSquareQuote, Star } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { answerText } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { SharedEvaluation } from '#lib/portal.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('portal.forms') }));

	let selected = $state('');
	const people = $derived(data.home.people);
	const person = (id: string) => people.find((candidate) => candidate.id === id);
	const forms = $derived(data.home.forms.filter((form) => !selected || form.member_id === selected));
	const evaluations = $derived(data.evaluations.filter((evaluation) => !selected || evaluation.member_id === selected));
	let opened = $state<SharedEvaluation | null>(null);
	const shown = (answer: SharedEvaluation['answers'][number]) => answerText({ field_type: answer.field_type, options: answer.options } as never, answer.value, t('common.yes'), t('common.no'));
	const scores = (evaluation: SharedEvaluation) => evaluation.answers.filter((answer) => answer.field_type !== 'text' && answer.field_type !== 'textarea' && shown(answer));
	const comments = (evaluation: SharedEvaluation) => evaluation.answers.filter((answer) => (answer.field_type === 'text' || answer.field_type === 'textarea') && shown(answer));
</script>

<div class="grid min-w-0 gap-4">
	<PersonFilter {people} bind:selected viewer={data.home.viewer} />
	<div class="grid min-w-0 items-start gap-4 {data.evaluations.length ? 'xl:grid-cols-2' : 'max-w-3xl'}">
		<section class="card" aria-labelledby="forms-title">
			<div class="card-h"><ClipboardList size={17} aria-hidden="true" /><h2 id="forms-title">{t('portal.forms_open')}</h2>{#if forms.length}<span class="badge badge-sm badge-error ml-auto">{t('portal.forms_to_fill', { n: forms.length })}</span>{/if}</div>
			<div class="divide-y divide-rule">
				{#each forms as form (`${form.id}-${form.member_id}`)}
					<div class="flex min-h-16 items-center gap-3 px-5 py-3">
						<ClubCrest name={person(form.member_id)?.name ?? ''} logoUrl={person(form.member_id)?.photo_url} size={36} />
						<span class="min-w-0 flex-1"><b class="block text-ink">{form.title}</b><span class="block text-xs text-muted">{person(form.member_id)?.name}{form.closes_at ? ` · ${t('pubform.closes', { date: formatDateTime(form.closes_at) })}` : ''}</span></span>
						<a class="btn btn-primary btn-sm" href="/parent/forms/{form.id}/{form.member_id}">{t('portal.form_fill')}</a>
					</div>
				{:else}
					<p class="flex items-center gap-2 px-5 py-4 text-sm text-muted"><CircleCheck size={16} class="text-ok" aria-hidden="true" />{t('portal.no_forms')}</p>
				{/each}
			</div>
		</section>

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
