<script lang="ts">
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ChargeForm, { lineTotal, newLine, type LineDraft } from '#lib/components/finance/ChargeForm.svelte';
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
	usePageHeader(() => ({ title: t('chg.new.title'), context: t('chg.title') }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

<svelte:head><title>{pageTitle(t('chg.new.title'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="mx-auto grid max-w-4xl gap-5"
	use:enhance={({ cancel }) => {
		tried = true;
		if (!complete) {
			cancel();
			return;
		}
		busy = true;
		leaving = true;
		return async ({ update }) => {
			await update({ reset: false });
			busy = false;
			leaving = false;
		};
	}}>
	<input type="hidden" name="payload" value={payload} />
	{#if failure}<Alert kind="error" title={t('chg.d.failed')}>{failure}</Alert>{/if}
	<ChargeForm bind:memberId bind:lines bind:dueDate bind:notes {errors} {tried} />
	<div class="flex flex-wrap justify-between gap-2">
		<a class="btn btn-outline gap-2" href="/finance/charges"><ArrowLeft size={16} /> {t('common.cancel')}</a>
		<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {t('chg.form.save_draft')}</button>
	</div>
</form>
