<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, CalendarPlus, ExternalLink, Plus, Save, Send, Trash2, Wand2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { billingBadge, day, installmentBadge, money, schedule, type Installment } from '#lib/sponsor-billing.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();

	type Row = { key: number; id?: string; due_date: string; amount: string; locked: boolean; source?: Installment };
	let seq = 0;
	const fromAgreement = (): Row[] => (data.agreement?.installments ?? []).map((i) => ({ key: ++seq, id: i.id, due_date: i.due_date, amount: i.amount, locked: !!i.order_id, source: i }));
	let contactName = $state(data.agreement?.contact_name ?? '');
	let contactEmail = $state(data.agreement?.contact_email ?? '');
	let description = $state(data.agreement?.description ?? '');
	let leadDays = $state(data.agreement?.lead_days ?? 30);
	let graceDays = $state(data.agreement?.grace_days ?? 14);
	let rows = $state<Row[]>(fromAgreement());
	let genTotal = $state('');
	let genCount = $state(4);
	let genFirst = $state('');
	let genEvery = $state(3);
	let busy = $state(false);

	// After a save the page data is new: show what the server has.
	$effect(() => {
		const agreement = data.agreement;
		if (!agreement) return;
		rows = agreement.installments.map((i) => ({ key: ++seq, id: i.id, due_date: i.due_date, amount: i.amount, locked: !!i.order_id, source: i }));
		contactName = agreement.contact_name;
		contactEmail = agreement.contact_email;
	});

	const payload = $derived(
		JSON.stringify({
			contact_name: contactName.trim(),
			contact_email: contactEmail.trim(),
			description: description.trim(),
			lead_days: Number(leadDays) || 0,
			grace_days: Number(graceDays) || 0,
			installments: rows.map((r) => ({ ...(r.id ? { id: r.id } : {}), due_date: r.due_date, amount: String(r.amount) }))
		})
	);
	const total = $derived(rows.reduce((n, r) => n + (Number(r.amount) || 0), 0));
	const complete = $derived(!!contactName.trim() && contactEmail.includes('@') && rows.length > 0 && rows.every((r) => r.due_date && Number(r.amount) > 0));
	const failure = $derived(form && 'op' in form && form.op === 'save' ? String(form.message || t('common.error.generic')) : '');
	const sendFailure = $derived(form && 'op' in form && form.op === 'send' ? String(form.message || t('common.error.generic')) : '');
	const errors = $derived<Record<string, string>>(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const add = () => (rows = [...rows, { key: ++seq, due_date: '', amount: '', locked: false }]);
	const generate = () => {
		const kept = rows.filter((r) => r.locked);
		rows = [...kept, ...schedule(Number(genTotal), Number(genCount), genFirst, Number(genEvery)).map((r) => ({ key: ++seq, ...r, locked: false }))];
	};
	const status = $derived(data.agreement?.billing ?? data.sponsor.billing);

	usePageHeader(() => ({ title: data.sponsor.name, context: t('spb.title'), actions }));
</script>

<svelte:head><title>{pageTitle(`${data.sponsor.name} · ${t('spb.title')}`)}</title></svelte:head>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/finance/sponsors"><ArrowLeft size={16} /> {t('spb.back')}</a>
{/snippet}

<div class="grid max-w-4xl gap-5">
	<section class="card card-body gap-2">
		<div class="flex flex-wrap items-center gap-3">
			<span class="badge {billingBadge(status.status)}">{t(`spb.status.${status.status}`, { n: status.overdue_days })}</span>
			{#if data.sponsor.status === 'suspended'}<span class="text-sm text-error">{t('spb.suspended_help', { n: data.agreement?.grace_days ?? 0 })}</span>{/if}
			{#if status.next_due_date}<span class="text-sm text-muted">{t('spb.next_due', { date: day(status.next_due_date) })}</span>{/if}
			{#if Number(status.open_amount) > 0}<span class="ml-auto font-mono text-sm font-semibold">{t('spb.open_amount', { amount: money(status.open_amount) })}</span>{/if}
		</div>
	</section>

	{#if form && 'done' in form && form.done}<Alert kind="success">{t(`spb.done.${form.done}`)}</Alert>{/if}
	{#if sendFailure}<Alert kind="error">{sendFailure}</Alert>{/if}

	{#if data.agreement}
		<section class="card" aria-labelledby="spb-installments">
			<div class="card-h"><CalendarPlus size={17} aria-hidden="true" /><h2 id="spb-installments">{t('spb.installments')}</h2></div>
			<div class="overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('spb.col.date')}</th><th class="text-right">{t('spb.col.amount')}</th><th class="text-right">{t('spb.col.paid')}</th><th>{t('spb.col.status')}</th><th>{t('spb.col.invoice')}</th><th><span class="sr-only">{t('spb.col.actions')}</span></th></tr></thead>
					<tbody>
						{#each data.agreement.installments as i (i.id)}
							<tr>
								<td class="font-mono text-xs whitespace-nowrap">{day(i.due_date)}</td>
								<td class="text-right font-mono text-sm">{money(i.amount)}</td>
								<td class="text-right font-mono text-sm">{money(i.amount_paid)}</td>
								<td><span class="badge badge-sm {installmentBadge(i.status)}">{t(`spb.inst.${i.status}`, { n: i.overdue_days })}</span></td>
								<td class="font-mono text-xs">
									{#if i.order_id && data.canSeeInvoices}<a class="link inline-flex items-center gap-1" href="/finance/charges/{i.order_id}">{i.invoice_number ?? '–'} <ExternalLink size={12} aria-hidden="true" /></a>{:else}{i.invoice_number ?? '–'}{/if}
								</td>
								<td class="text-right">
									{#if i.status !== 'paid'}
										<form method="POST" action="?/send" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; }; }}>
											<input type="hidden" name="installment_id" value={i.id} />
											<button class="btn btn-outline btn-xs gap-1" type="submit" disabled={busy}><Send size={13} aria-hidden="true" /> {i.order_id ? t('spb.resend') : t('spb.send')}</button>
										</form>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<form
		method="POST"
		action="?/save"
		class="grid gap-5"
		novalidate
		use:enhance={({ cancel }) => {
			if (!complete) {
				cancel();
				return;
			}
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
			};
		}}>
		<input type="hidden" name="payload" value={payload} />
		{#if failure}<Alert kind="error">{failure}</Alert>{/if}

		<section class="card card-body gap-3" aria-labelledby="spb-agreement">
			<h2 id="spb-agreement" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{data.agreement ? t('spb.agreement') : t('spb.new_agreement')}</h2>
			<p class="text-xs text-muted">{t('spb.agreement_help')}</p>
			<div class="grid gap-3 sm:grid-cols-2">
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.contact_name')}</span><input class="input input-bordered w-full {errors.contact_name ? 'input-error' : ''}" maxlength="150" bind:value={contactName} /></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.contact_email')}</span><input class="input input-bordered w-full {errors.contact_email ? 'input-error' : ''}" type="email" maxlength="254" bind:value={contactEmail} /></label>
				<label class="grid gap-1 sm:col-span-2"><span class="text-xs font-semibold text-muted">{t('spb.description')}</span><input class="input input-bordered w-full" maxlength="150" placeholder={t('spb.description_ph')} bind:value={description} /></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.lead_days')}</span><input class="input input-bordered w-full" type="number" min="0" max="365" bind:value={leadDays} /><span class="text-xs text-muted">{t('spb.lead_help')}</span></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.grace_days')}</span><input class="input input-bordered w-full" type="number" min="0" max="365" bind:value={graceDays} /><span class="text-xs text-muted">{t('spb.grace_help')}</span></label>
			</div>
		</section>

		<section class="card card-body gap-3" aria-labelledby="spb-schedule">
			<h2 id="spb-schedule" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('spb.schedule')}</h2>
			{#if errors.installments}<p class="text-xs text-error">{errors.installments}</p>{/if}
			<div class="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-[repeat(4,minmax(0,1fr))_auto] sm:items-end">
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.gen.total')}</span><input class="input input-bordered w-full" type="number" min="0" step="0.01" bind:value={genTotal} /></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.gen.count')}</span><input class="input input-bordered w-full" type="number" min="1" max="24" bind:value={genCount} /></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.gen.first')}</span><input class="input input-bordered w-full" type="date" bind:value={genFirst} /></label>
				<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.gen.every')}</span><input class="input input-bordered w-full" type="number" min="1" max="12" bind:value={genEvery} /></label>
				<button class="btn btn-outline gap-2" type="button" onclick={generate} disabled={!(Number(genTotal) > 0) || !genFirst}><Wand2 size={16} aria-hidden="true" /> {t('spb.gen.fill')}</button>
			</div>
			<div class="grid gap-2">
				{#each rows as r, i (r.key)}
					<div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
						<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.col.date')}</span><input class="input input-bordered w-full" type="date" bind:value={r.due_date} disabled={r.locked} /></label>
						<label class="grid gap-1"><span class="text-xs font-semibold text-muted">{t('spb.col.amount')}</span><input class="input input-bordered w-full {errors[`installments.${i}`] ? 'input-error' : ''}" type="number" min="0.01" step="0.01" inputmode="decimal" bind:value={r.amount} disabled={r.locked} /></label>
						<button class="btn btn-outline btn-error btn-square" type="button" aria-label={t('spb.remove')} title={r.locked ? t('spb.locked') : t('spb.remove')} disabled={r.locked} onclick={() => (rows = rows.filter((x) => x.key !== r.key))}><Trash2 size={16} /></button>
						{#if errors[`installments.${i}`]}<p class="text-xs text-error sm:col-span-3">{errors[`installments.${i}`]}</p>{/if}
					</div>
				{/each}
			</div>
			<div class="flex flex-wrap items-center gap-3">
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={add}><Plus size={14} aria-hidden="true" /> {t('spb.add_row')}</button>
				<span class="ml-auto font-mono text-sm">{t('spb.total')}: {money(total)}</span>
			</div>
			{#if rows.some((r) => r.locked)}<p class="text-xs text-muted">{t('spb.locked_help')}</p>{/if}
		</section>

		<div><button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} aria-hidden="true" /> {t('common.save')}</button></div>
	</form>
</div>
