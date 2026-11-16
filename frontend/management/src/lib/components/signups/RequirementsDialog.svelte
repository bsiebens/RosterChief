<script lang="ts">
	import { untrack } from 'svelte';
	import Alert from '#lib/components/Alert.svelte';
	import DocumentsCard from '#lib/components/members/detail/DocumentsCard.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Checklist } from '#lib/requirements.ts';
	import type { SignupRow } from '#lib/signups.ts';

	/**
	 * A sign-up's requirements in a pop-up: the member's documents and questions for the sign-up's season, with receive / answer / waive / reopen,
	 * so staff need not open the member first. The page must spread `requirementActions` into its actions. `version` is anything that changes when
	 * the page's data reloads (e.g. its rows), so the list here is fetched again after every action.
	 */
	let { open = $bindable(false), row, form, version }: { open: boolean; row: SignupRow | null; form: unknown; version: unknown } = $props();

	// The page keeps the result of its last action: a result that was already there when this dialog opened (an earlier save, maybe for someone else)
	// is not ours, so only a newer one reaches the card (its "Saved" banner and errors).
	let baseline = $state.raw<unknown>(null);
	let openedFor = '';
	$effect(() => {
		const key = open && row ? row.membership_id : '';
		if (key === openedFor) return;
		openedFor = key;
		untrack(() => (baseline = form));
	});
	const shown = $derived(form === baseline ? null : form);

	let checklist = $state<Checklist | null>(null);
	let failed = $state(false);
	let loading = $state(false);

	$effect(() => {
		void version;
		const r = open ? row : null;
		if (!r) return;
		untrack(() => (loading = !checklist));
		fetch(`/api/v1/requirements/members/${r.member.id}/checklist?season_id=${r.season_id}`, { headers: { accept: 'application/json' } })
			.then((res) => (res.ok ? (res.json() as Promise<Checklist>) : Promise.reject(new Error(String(res.status)))))
			.then((c) => ((checklist = c), (failed = false)))
			.catch(() => (failed = true))
			.finally(() => (loading = false));
	});
	$effect(() => {
		if (!open) checklist = null;
	});
</script>

<Modal bind:open title={t('signups.req_title', { name: row?.member.name ?? '' })} wide>
	{#if failed}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if loading || !checklist}
		<p class="text-sm text-muted">{t('common.loading')}</p>
	{:else}
		<DocumentsCard {checklist} canManage form={shown} actionBase="?" />
	{/if}
	<div class="modal-action mt-4 flex justify-end">
		<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.close')}</button>
	</div>
</Modal>
