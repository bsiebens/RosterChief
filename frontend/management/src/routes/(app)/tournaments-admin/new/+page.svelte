<script lang="ts">
	import { goto } from '$app/navigation';
	import { Plus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import TournamentFields from '#lib/components/tournaments/TournamentFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { Runner, TBASE, type TournamentDetail } from '#lib/tournaments.svelte.ts';
	import { formOf, payload } from '#lib/tournaments-form.ts';

	let { data } = $props();
	const run = new Runner();
	let v = $state(formOf());

	async function create(e: SubmitEvent) {
		e.preventDefault();
		let id = '';
		const ok = await run.run(() => api<TournamentDetail>('POST', '/tournaments', { body: payload(v) }), (d) => void (id = d?.id ?? ''), { reload: false });
		if (ok && id) await goto(`${TBASE}/${id}`);
	}

	usePageHeader(() => ({ title: t('tn.add'), context: t('tn.title') }));
</script>

<svelte:head><title>{pageTitle(t('tn.add'))}</title></svelte:head>

<form class="grid max-w-3xl gap-4" onsubmit={create}>
	<section class="card card-body card-hl gap-3">
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<TournamentFields bind:v errors={run.fields} teams={data.teams} />
		<div class="flex justify-end gap-2">
			<a class="btn btn-outline" href={TBASE}>{t('common.cancel')}</a>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.create')}</button>
		</div>
	</section>
</form>
