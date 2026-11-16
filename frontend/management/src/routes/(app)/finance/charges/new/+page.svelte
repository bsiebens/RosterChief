<script lang="ts">
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, ClipboardList, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ChargeForm, { lineTotal, newLine, type LineDraft } from '#lib/components/finance/ChargeForm.svelte';
	import { day, money } from '#lib/charges.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { form } = $props();

	let memberId = $state('');
	let lines = $state<LineDraft[]>([newLine()]);
	let dueDate = $state('');
	let notes = $state('');
	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);

	const dirty = $derived(!!memberId || !!notes || !!dueDate || lines.some((l) => l.description || l.unit_price));
	const payload = $derived(
		JSON.stringify({
			member_id: memberId,
			lines: lines.map((l) => ({ description: l.description.trim(), quantity: Number(l.quantity) || 0, unit_price: String(l.unit_price) })),
			due_date: dueDate || null,
			notes
		})
	);
	const complete = $derived(!!memberId && lines.every((l) => l.description.trim() && lineTotal(l) > 0));
	const failure = $derived(form && 'op' in form ? String(form.message || t('common.error.generic')) : '');
	const errors = $derived<Record<string, string>>(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});

	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});
	const filled = $derived(lines.filter((l) => l.description.trim() || Number(l.unit_price) > 0));
	const total = $derived(lines.reduce((sum, l) => sum + lineTotal(l), 0));

	usePageHeader(() => ({ title: t('chg.new.title'), context: t('chg.title'), actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

<svelte:head><title>{pageTitle(t('chg.new.title'))}</title></svelte:head>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/finance/charges"><ArrowLeft size={16} /> {t('chg.d.back')}</a>
{/snippet}

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		tried = true;
		if (!complete) {
			cancel();
			return;
		}
		busy = true;
		leaving = true;
		return async ({ update, result }) => {
			await update({ reset: false });
			busy = false;
			if (result.type !== 'redirect') leaving = false;
		};
	}}>
	<input type="hidden" name="payload" value={payload} />
	{#if failure}<Alert kind="error" title={t('chg.d.failed')}>{failure}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="xl:col-span-2">
			<ChargeForm bind:memberId bind:lines bind:dueDate bind:notes {errors} {tried} />
		</div>
		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" /> {t('chg.new.summary')}</h2>
				<dl class="grid gap-1.5 text-sm">
					{#each filled as l (l.key)}
						<div class="flex justify-between gap-3"><dt><span class="font-mono text-muted">{Number(l.quantity) || 0} ×</span> {l.description.trim() || '…'}</dt><dd class="shrink-0 font-mono">{money(lineTotal(l))}</dd></div>
					{:else}
						<p class="text-muted">{t('chg.new.summary_empty')}</p>
					{/each}
					<div class="flex justify-between gap-3 border-t border-line pt-1 text-base font-semibold"><dt>{t('chg.form.total')}</dt><dd class="font-mono">{money(total)}</dd></div>
				</dl>
				<p class="text-xs text-muted">{dueDate ? t('chg.new.summary_due', { date: day(dueDate) }) : t('chg.new.summary_due_default')}. {t('chg.form.no_vat')}</p>
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('chg.form.save_draft')}</button>
					<a class="btn btn-outline w-full" href="/finance/charges">{t('common.cancel')}</a>
				</div>
			</section>
		</aside>
	</div>
</form>
