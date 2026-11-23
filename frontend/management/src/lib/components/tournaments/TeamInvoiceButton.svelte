<script lang="ts">
	import { ExternalLink, Receipt } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { invalidateAll } from '$app/navigation';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { money } from '#lib/shop.ts';

	type Doc = { id: string; number: string | null; total: string; paid: string; payment_status: string; cancelled: boolean; url: string };
	type State = { billable: boolean; managed: boolean; deposit: Doc | null; balance: Doc | null; can_deposit: boolean; can_balance: boolean };

	/** The invoices of a guest team in finance: what exists, what was paid, and making the deposit or balance invoice when it is not there yet. */
	let { teamId, invoices, canManage = false }: { teamId: string; invoices: State | null | undefined; canManage?: boolean } = $props();
	const rows = $derived<{ key: 'deposit' | 'balance'; doc: Doc | null; can: boolean }[]>([
		{ key: 'deposit', doc: invoices?.deposit ?? null, can: !!invoices?.can_deposit },
		{ key: 'balance', doc: invoices?.balance ?? null, can: !!invoices?.can_balance }
	]);
	let open = $state(false);
	let busy = $state(false);
	let error = $state('');

	async function create(which: 'deposit' | 'balance') {
		busy = true;
		error = '';
		const res = await api('POST', `/tournaments/teams/${teamId}/invoices/${which}`);
		busy = false;
		if (res.ok) await invalidateAll();
		else error = res.message || t('common.error.generic');
	}
</script>

{#if invoices?.billable}
	<button class="btn btn-outline btn-xs btn-square" type="button" title={t('tn.inv.title')} aria-label={t('tn.inv.title')} onclick={() => { error = ''; open = true; }}><Receipt size={13} aria-hidden="true" /></button>
{:else}
	<button class="btn btn-outline btn-xs btn-square" type="button" disabled aria-label={t('tn.inv.title')}><Receipt size={13} aria-hidden="true" /></button>
{/if}

<Modal bind:open title={t('tn.inv.title')}>
	<div class="grid gap-3">
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<p class="text-sm text-muted">{t('tn.inv.help')}</p>
		{#each rows as { key, doc, can } (key)}
			<div class="flex items-center justify-between gap-3 rounded-lg border border-line p-3">
				<div class="min-w-0">
					<p class="font-semibold text-ink">{t(`tn.inv.${key}`)}</p>
					{#if doc}
						<p class="text-sm text-muted">{doc.number ? `${doc.number} · ` : ''}{money(doc.total)} · {t('tn.inv.paid', { paid: money(doc.paid) })} · {t(`tn.inv.s.${doc.payment_status}`)}</p>
					{:else}
						<p class="text-sm text-muted">{can ? t('tn.inv.not_yet') : t('tn.inv.nothing')}</p>
					{/if}
				</div>
				{#if doc}
					<a class="btn btn-outline btn-xs gap-1" href={doc.url} target="_blank" rel="noopener"><ExternalLink size={13} aria-hidden="true" /> {t('tn.inv.open')}</a>
				{:else if can && canManage}
					<button class="btn btn-primary btn-xs" type="button" disabled={busy} onclick={() => create(key)}>{t('tn.inv.create')}</button>
				{/if}
			</div>
		{/each}
		<div class="modal-action mt-1 flex justify-end"><button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.close')}</button></div>
	</div>
</Modal>
