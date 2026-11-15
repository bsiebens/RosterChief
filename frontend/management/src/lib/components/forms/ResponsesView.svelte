<script lang="ts">
	import { enhance } from '$app/forms';
	import { Clock, Table, Download, Paperclip, Trash2, ClipboardList } from '@lucide/svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { answerFileHref, answerText, optionLabel, optionsOf, reportCsvHref, type Report, type ReportRow } from '#lib/forms.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/** The responses of one send: counts per choice, the table of answers, and who has not responded yet. Deleting posts to the page's `?/deleteSubmission`. */
	let { report, sendId }: { report: Report; sendId: string } = $props();
	const uid = $props.id();

	const yes = $derived(t('common.yes'));
	const no = $derived(t('common.no'));
	// Fields that have counts: choices, multiple choices and checkboxes, in column order.
	const summarised = $derived(report.columns.filter((c) => report.summaries[c.id] && ['choice', 'multichoice', 'checkbox'].includes(c.field_type)));
	const bars = (c: (typeof report.columns)[number]) => {
		const counts = report.summaries[c.id] ?? {};
		const known = c.field_type === 'checkbox' ? [{ value: 'True', label: yes }, { value: 'False', label: no }] : optionsOf(c);
		const rows = known.map((o) => ({ label: o.label, n: counts[o.value] ?? 0 }));
		for (const [k, n] of Object.entries(counts)) if (!known.some((o) => o.value === k)) rows.push({ label: c.field_type === 'checkbox' ? k : optionLabel(c, k), n });
		const max = Math.max(1, ...rows.map((r) => r.n));
		return { rows, max };
	};

	let target = $state<ReportRow | null>(null);
	let open = $state(false);
	let busy = $state(false);
	const who = (r: ReportRow) => r.member?.name ?? r.submitted_by?.name ?? t('forms.resp.anonymous');
</script>

<div class="grid gap-5">
	{#if summarised.length}
		<section class="card card-body gap-4" aria-labelledby="{uid}-sum">
			<h2 id="{uid}-sum" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardList size={16} class="shrink-0" aria-hidden="true" /> {t('forms.resp.summary')}</h2>
			<div class="grid gap-5 md:grid-cols-2">
				{#each summarised as c (c.id)}
					{@const b = bars(c)}
					<div class="grid content-start gap-2">
						<h3 class="text-sm font-semibold text-ink">{c.label}</h3>
						<ul class="grid gap-1.5">
							{#each b.rows as r (r.label)}
								<li class="grid grid-cols-[minmax(0,10rem)_1fr_2rem] items-center gap-2 text-sm">
									<span class="truncate" title={r.label}>{r.label}</span>
									<span class="h-4 rounded-md bg-surface"><span class="block h-full min-w-[3px] rounded-md bg-club" style="width: {(r.n / b.max) * 100}%" role="presentation"></span></span>
									<span class="text-right font-mono text-xs">{r.n}</span>
								</li>
							{/each}
						</ul>
					</div>
				{/each}
			</div>
		</section>
	{/if}

	<section class="card" aria-labelledby="{uid}-tbl">
		<header class="flex flex-wrap items-center justify-between gap-3 p-4 pb-2">
			<h2 id="{uid}-tbl" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Table size={16} class="shrink-0" aria-hidden="true" /> {t('forms.resp.table', { n: report.count })}</h2>
			{#if report.count}<a class="btn btn-outline btn-sm gap-2" href={reportCsvHref(sendId)} download><Download size={14} /> {t('forms.resp.csv')}</a>{/if}
		</header>
		{#if !report.rows.length}
			<p class="p-6 text-center text-sm text-muted">{t('forms.resp.empty')}</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('forms.resp.col.who')}</th><th>{t('forms.resp.col.at')}</th>
							{#each report.columns as c (c.id)}<th class={c.is_active ? '' : 'opacity-60'}>{c.label}</th>{/each}
							<th><span class="sr-only">{t('forms.col.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each report.rows as r (r.submission_id)}
							<tr class="align-top hover:bg-surface">
								<td class="whitespace-nowrap">
									<span class="font-semibold">{who(r)}</span>
									{#if r.on_behalf && r.submitted_by}<div class="text-xs text-muted">{t('forms.resp.on_behalf', { name: r.submitted_by.name })}</div>{/if}
								</td>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{formatDateTime(r.submitted_at)}</td>
								{#each report.columns as c (c.id)}
									<td class="max-w-64 text-sm">
										{#if c.field_type === 'file'}
											{#if r.files[c.id]}<a class="link inline-flex items-center gap-1" href={answerFileHref(r.files[c.id].answer_id)} download><Paperclip size={13} aria-hidden="true" /> {r.files[c.id].name}</a>{/if}
										{:else}
											<span class="break-words whitespace-pre-line">{answerText(c, r.values[c.id], yes, no)}</span>
										{/if}
									</td>
								{/each}
								<td class="text-right"><button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('forms.resp.delete_named', { name: who(r) })} onclick={() => ((target = r), (open = true))}><Trash2 size={13} /></button></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>

	<section class="card card-body gap-2" aria-labelledby="{uid}-nr">
		<h2 id="{uid}-nr" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Clock size={16} class="shrink-0" aria-hidden="true" /> {t('forms.resp.not_responded', { n: report.not_responded.length })}</h2>
		{#if report.not_responded.length}
			<ul class="flex flex-wrap gap-1.5">
				{#each report.not_responded as m (m.id)}<li class="rounded-full border border-line bg-subhead px-3 py-0.5 text-sm"><a class="link" href="/members/{m.id}">{m.name}</a></li>{/each}
			</ul>
		{:else}
			<p class="text-sm text-muted">{t('forms.resp.all_responded')}</p>
		{/if}
	</section>
</div>

<Modal bind:open title={t('forms.resp.delete_title')}>
	<form
		method="POST"
		action="?/deleteSubmission"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				open = false;
			};
		}}>
		<input type="hidden" name="id" value={target?.submission_id ?? ''} />
		<p>{t('forms.resp.delete_body', { name: target ? who(target) : '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
